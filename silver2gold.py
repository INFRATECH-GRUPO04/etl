import csv
import io
import json
import os
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import boto3

# Lê o .env, se existir. Na EC2, o boto3 usa a IAM Role.
if os.path.exists('.env'):
    for linha in open('.env', encoding='utf-8'):
        if '=' in linha and not linha.lstrip().startswith('#'):
            chave, valor = linha.strip().split('=', 1)
            os.environ.setdefault(chave, valor.strip('"\''))

BUCKET = os.getenv('S3_BUCKET')
SILVER = os.getenv('S3_SILVER_PREFIX', '02-silver/').strip('/') + '/'
GOLD = os.getenv('S3_GOLD_PREFIX', '03-gold/').strip('/') + '/'
OFFLINE_TIMEOUT = int(os.getenv('OFFLINE_TIMEOUT_SECONDS', '900'))
REDE_TIMEOUT = int(os.getenv('REDE_MAX_INTERVAL_SECONDS', '30'))
LIMITE_CPU = float(os.getenv('LIMITE_CPU_PCT', '80'))
LIMITE_RAM = float(os.getenv('LIMITE_RAM_PCT', '80'))
LIMITE_DISCO = float(os.getenv('LIMITE_DISCO_PCT', '80'))

s3 = boto3.client('s3')
servidores = defaultdict(dict)

# Lê todas as coletas da Silver; a data/hora é considerada UTC.
for pagina in s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=SILVER + 'metricas/'):
    for objeto in pagina.get('Contents', []):
        if not objeto['Key'].endswith('_dados.csv'):
            continue
        texto = s3.get_object(Bucket=BUCKET, Key=objeto['Key'])['Body'].read().decode('utf-8-sig')
        for linha in csv.DictReader(io.StringIO(texto)):
            try:
                empresa = int(linha['id_empresa'])
                servidor = int(linha['id_servidor'])
                data = datetime.fromisoformat(linha['data_hora'].replace('Z', '+00:00'))
                if data.tzinfo is None:
                    data = data.replace(tzinfo=timezone.utc)
                data = data.astimezone(timezone.utc)
                linha['_data'] = data
                linha['_cpu'] = float(linha['cpu'])
                linha['_ram'] = float(linha['ram'])
                linha['_disco'] = float(linha['disco'])
                servidores[(empresa, servidor)][data] = linha
            except (KeyError, ValueError, TypeError):
                continue

agora = datetime.now(timezone.utc)
inicio = agora - timedelta(hours=24)
empresas = sorted({empresa for empresa, servidor in servidores})

for empresa in empresas:
    atuais = []
    recursos = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    rede = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    upload_atual = download_atual = 0

    for (id_empresa, id_servidor), amostras in servidores.items():
        if id_empresa != empresa:
            continue
        lista = sorted(amostras.values(), key=lambda x: x['_data'])
        ultima = lista[-1]
        taxas = {}
        anterior = None

        for amostra in lista:
            data = amostra['_data']
            minuto = data.replace(second=0, microsecond=0).strftime('%Y-%m-%d %H:%M')

            # Médias de CPU, RAM e disco por minuto, juntando os servidores da empresa.
            if inicio <= data <= agora:
                valores = recursos[minuto]
                for pos, campo in enumerate(('_cpu', '_ram', '_disco')):
                    valores[pos * 2] += amostra[campo]
                    valores[pos * 2 + 1] += 1

            # Calcula Mbps pela diferença dos contadores entre duas coletas.
            if anterior:
                segundos = (data - anterior['_data']).total_seconds()
                try:
                    enviados = int(amostra['bytes_enviados']) - int(anterior['bytes_enviados'])
                    recebidos = int(amostra['bytes_recebidos']) - int(anterior['bytes_recebidos'])
                    # if 0 < segundos <= TIMEOUT and enviados >= 0 and recebidos >= 0:
                    if 0 < segundos <= REDE_TIMEOUT and enviados >= 0 and recebidos >= 0:
                        up = enviados * 8 / segundos / 1_000_000
                        down = recebidos * 8 / segundos / 1_000_000
                        taxas[data] = (up, down)
                        if inicio <= data <= agora:
                            valores = rede[minuto][id_servidor]
                            valores[0] += up
                            valores[1] += down
                            valores[2] += 1
                except (KeyError, ValueError, TypeError):
                    pass
            anterior = amostra

        atraso = (agora - ultima['_data']).total_seconds()
        if atraso > OFFLINE_TIMEOUT:
            status = 'offline'
        elif ultima['_cpu'] >= LIMITE_CPU or ultima['_ram'] >= LIMITE_RAM or ultima['_disco'] >= LIMITE_DISCO:
            status = 'atencao'
        else:
            status = 'online'

        up, down = taxas.get(ultima['_data'], (None, None))
        if status != 'offline' and up is not None:
            upload_atual += up
            download_atual += down

        atuais.append({
            'id_servidor': id_servidor,
            'nome': ultima.get('username') or f'Servidor {id_servidor}',
            'status': status,
            'ultima_coleta': ultima['data_hora'],
            'cpu_pct': ultima['_cpu'],
            'ram_pct': ultima['_ram'],
            'disco_pct': ultima['_disco'],
            'upload_mbps': round(up, 2) if up is not None else None,
            'download_mbps': round(down, 2) if down is not None else None,
        })

    # Transforma os acumuladores em pontos prontos para os gráficos da dashboard.
    grafico_recursos = []
    for minuto, v in sorted(recursos.items()):
        grafico_recursos.append({
            'horario': minuto,
            'cpu_pct': round(v[0] / v[1], 2) if v[1] else None,
            'ram_pct': round(v[2] / v[3], 2) if v[3] else None,
            'disco_pct': round(v[4] / v[5], 2) if v[5] else None,
        })

    grafico_rede = []
    for minuto, por_servidor in sorted(rede.items()):
        upload = download = 0
        for valores in por_servidor.values():
            upload += valores[0] / valores[2]
            download += valores[1] / valores[2]
        grafico_rede.append({
            'horario': minuto,
            'upload_mbps': round(upload, 2),
            'download_mbps': round(download, 2),
        })

    online = sum(1 for s in atuais if s['status'] != 'offline')
    atencao = sum(1 for s in atuais if s['status'] == 'atencao')
    offline = sum(1 for s in atuais if s['status'] == 'offline')
    documento = {
        'id_empresa': empresa,
        'gerado_em': agora.isoformat(),
        'kpis': {
            'total_servidores': len(atuais),
            'servidores_online': online,
            'servidores_atencao': atencao,
            'servidores_offline': offline,
            'servidores_para_investigar': atencao + offline,
            'upload_mbps': round(upload_atual, 2),
            'download_mbps': round(download_atual, 2),
            'trafego_total_mbps': round(upload_atual + download_atual, 2),
        },
        'servidores': sorted(atuais, key=lambda x: x['id_servidor']),
        'graficos': {
            'recursos_24h': grafico_recursos,
            'rede_24h': grafico_rede,
        },
    }

    chave = f'{GOLD}empresa-{empresa}/dashboard.json'
    s3.put_object(
        Bucket=BUCKET,
        Key=chave,
        Body=json.dumps(documento, ensure_ascii=False, indent=2),
        ContentType='application/json'
    )
    print(f'Gerado: {chave} ({len(atuais)} servidores)')

print('Silver -> Gold concluído.')
