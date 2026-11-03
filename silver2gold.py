import csv
import io
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
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
FUSO_LOCAL = ZoneInfo(os.getenv('TIMEZONE', 'America/Sao_Paulo'))

s3 = boto3.client('s3')
servidores = defaultdict(dict)

# Lê todas as coletas da Silver; horários sem fuso são considerados locais.
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
                    data = data.replace(tzinfo=FUSO_LOCAL)
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
    upload_atual = download_atual = 0

    for (id_empresa, id_servidor), amostras in servidores.items():
        if id_empresa != empresa:
            continue
        lista = sorted(amostras.values(), key=lambda x: x['_data'])
        ultima = lista[-1]
        taxas = {}
        recursos = {}
        anterior = None

        for amostra in lista:
            data = amostra['_data']
            minuto = data.astimezone(FUSO_LOCAL).replace(second=0, microsecond=0)

            # Guarda a última captura de cada minuto para o gráfico deste servidor.
            if inicio <= data <= agora:
                recursos[minuto] = amostra

            # Calcula Mbps pela diferença dos contadores entre duas coletas.
            if anterior:
                segundos = (data - anterior['_data']).total_seconds()
                try:
                    enviados = int(amostra['bytes_enviados']) - int(anterior['bytes_enviados'])
                    recebidos = int(amostra['bytes_recebidos']) - int(anterior['bytes_recebidos'])
                    if 0 < segundos <= REDE_TIMEOUT and enviados >= 0 and recebidos >= 0:
                        up = enviados * 8 / segundos / 1_000_000
                        down = recebidos * 8 / segundos / 1_000_000
                        taxas[data] = (up, down)
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

        # Transforma as capturas em pontos prontos para o gráfico individual.
        grafico_recursos = []
        for minuto, amostra in sorted(recursos.items()):
            grafico_recursos.append({
                'horario': minuto.strftime('%Y-%m-%d %H:%M'),
                'cpu_pct': amostra['_cpu'],
                'ram_pct': amostra['_ram'],
                'disco_pct': amostra['_disco'],
            })

        # Usa o último ponto do gráfico para manter os valores atuais iguais ao histórico.
        if grafico_recursos:
            ultimo_ponto = grafico_recursos[-1]
            metricas_atuais = {
                'cpu_pct': ultimo_ponto['cpu_pct'],
                'ram_pct': ultimo_ponto['ram_pct'],
                'disco_pct': ultimo_ponto['disco_pct'],
            }
        else:
            metricas_atuais = {
                'cpu_pct': None,
                'ram_pct': None,
                'disco_pct': None,
            }

        atuais.append({
            'id_servidor': id_servidor,
            'nome': ultima.get('username') or f'Servidor {id_servidor}',
            'status': status,
            'ultima_coleta': ultima['data_hora'],
            'metricas_atuais': metricas_atuais,
            'graficos': {
                'recursos_24h': grafico_recursos,
            },
        })

    online = sum(1 for s in atuais if s['status'] == 'online')
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
            'upload_mbps': round(upload_atual, 2),
            'download_mbps': round(download_atual, 2),
            'trafego_mbps': round(upload_atual + download_atual, 2),
        },
        'servidores': sorted(atuais, key=lambda x: x['id_servidor']),
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
