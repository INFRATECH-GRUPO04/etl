import csv
import io
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict
import boto3


def carregar_configuracao():
    # Lê o .env, se existir. Na EC2/Lambda, o boto3 usa a IAM Role.
    if os.path.exists('.env'):
        for linha in open('.env', encoding='utf-8'):
            if '=' in linha and not linha.lstrip().startswith('#'):
                chave, valor = linha.strip().split('=', 1)
                os.environ.setdefault(chave, valor.strip('"\''))

    return {
        'bucket': os.getenv('S3_BUCKET'),
        'bronze': os.getenv('S3_BRONZE_PREFIX', '01-bronze/').strip('/') + '/',
        'silver': os.getenv('S3_SILVER_PREFIX', '02-silver/').strip('/') + '/',
        'gold': os.getenv('S3_GOLD_PREFIX', '03-gold/').strip('/') + '/',
        'offline_timeout': int(os.getenv('OFFLINE_TIMEOUT_SECONDS', '900')),
        'rede_timeout': int(os.getenv('REDE_MAX_INTERVAL_SECONDS', '90')),
        'fuso': ZoneInfo(os.getenv('TIMEZONE', 'America/Sao_Paulo')),
    }


def carregar_processos_recentes(s3, bucket, bronze):
    """Busca apenas o *_processos.csv mais recente de cada servidor na Bronze."""
    ultimos_arquivos = {}
    for pagina in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=bronze):
        for objeto in pagina.get('Contents', []):
            chave = objeto['Key']
            match = re.search(r'empresa-(\d+)_servidor-(\d+)_processos\.csv$', chave)
            if not match:
                continue
            par = (int(match[1]), int(match[2]))
            atual = ultimos_arquivos.get(par)
            if not atual or objeto['LastModified'] > atual['LastModified']:
                ultimos_arquivos[par] = objeto

    processos_por_servidor = {}
    for par, objeto in ultimos_arquivos.items():
        texto = s3.get_object(Bucket=bucket, Key=objeto['Key'])['Body'].read().decode('utf-8-sig')
        vistos = set()
        lista = []
        for linha in csv.DictReader(io.StringIO(texto)):
            try:
                pid = int(linha['PID'])
                nome = (linha.get('Nome') or '').strip()
                user = (linha.get('Username') or '').strip()
                if not nome or (pid, nome) in vistos:
                    continue
                vistos.add((pid, nome))
                lista.append({'pid': pid, 'nome': nome, 'user': user})
                if len(lista) >= 20:
                    break
            except (KeyError, ValueError, TypeError):
                continue
        processos_por_servidor[par] = lista
    return processos_por_servidor


def run(s3=None, *, agora=None):
    cfg = carregar_configuracao()
    bucket = cfg['bucket']
    if not bucket:
        raise RuntimeError('Defina S3_BUCKET no ambiente ou no arquivo .env.')
    if s3 is None:
        s3 = boto3.client('s3')

    fuso = cfg['fuso']
    servidores = defaultdict(dict)

    # Lê todas as coletas da Silver; horários sem fuso são considerados locais.
    for pagina in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=cfg['silver'] + 'metricas/'):
        for objeto in pagina.get('Contents', []):
            if not objeto['Key'].endswith('_dados.csv'):
                continue
            texto = s3.get_object(Bucket=bucket, Key=objeto['Key'])['Body'].read().decode('utf-8-sig')
            for linha in csv.DictReader(io.StringIO(texto)):
                try:
                    empresa = int(linha['id_empresa'])
                    servidor = int(linha['id_servidor'])
                    data = datetime.fromisoformat(linha['data_hora'].replace('Z', '+00:00'))
                    if data.tzinfo is None:
                        data = data.replace(tzinfo=fuso)
                    data = data.astimezone(timezone.utc)
                    linha['_data'] = data
                    linha['_cpu'] = float(linha['cpu'])
                    linha['_ram'] = float(linha['ram'])
                    linha['_disco'] = float(linha['disco'])
                    if not all(math.isfinite(linha[c]) for c in ('_cpu', '_ram', '_disco')):
                        continue
                    servidores[(empresa, servidor)][data] = linha
                except (KeyError, ValueError, TypeError):
                    continue

    processos_por_servidor = carregar_processos_recentes(s3, bucket, cfg['bronze'])

    instante_atual = agora or datetime.now(timezone.utc)
    if instante_atual.tzinfo is None:
        instante_atual = instante_atual.replace(tzinfo=timezone.utc)
    instante_atual = instante_atual.astimezone(timezone.utc)
    inicio = instante_atual - timedelta(hours=24)
    empresas = sorted({empresa for empresa, _ in servidores})

    for empresa in empresas:
        noc_servidores = []

        for (id_empresa, id_servidor), amostras in servidores.items():
            if id_empresa != empresa:
                continue
            lista = sorted(amostras.values(), key=lambda x: x['_data'])
            ultima = lista[-1]
            taxas = {}
            recursos = {}
            rede_por_minuto = {}
            anterior = None

            for amostra in lista:
                data = amostra['_data']
                minuto = data.astimezone(fuso).replace(second=0, microsecond=0)

                # Guarda a última captura de cada minuto para o gráfico deste servidor.
                if inicio <= data <= instante_atual:
                    recursos[minuto] = amostra

                # Calcula Mbps pela diferença dos contadores entre duas coletas.
                if anterior:
                    segundos = (data - anterior['_data']).total_seconds()
                    try:
                        enviados = int(amostra['bytes_enviados']) - int(anterior['bytes_enviados'])
                        recebidos = int(amostra['bytes_recebidos']) - int(anterior['bytes_recebidos'])
                        if 0 < segundos <= cfg['rede_timeout'] and enviados >= 0 and recebidos >= 0:
                            up = enviados * 8 / segundos / 1_000_000
                            down = recebidos * 8 / segundos / 1_000_000
                            if math.isfinite(up) and math.isfinite(down):
                                taxas[data] = (up, down)
                                if inicio <= data <= instante_atual:
                                    rede_por_minuto[minuto] = (down, up)
                    except (KeyError, ValueError, TypeError):
                        pass
                anterior = amostra

            atraso = (instante_atual - ultima['_data']).total_seconds()
            up, down = taxas.get(ultima['_data'], (None, None))

            # Transforma as capturas em pontos prontos para os gráficos da NOC.
            series_24h_noc = []
            for minuto, amostra in sorted(recursos.items()):
                horario_str = minuto.strftime('%Y-%m-%d %H:%M')
                down_m, up_m = rede_por_minuto.get(minuto, (None, None))
                series_24h_noc.append({
                    'horario': horario_str,
                    'cpu_pct': round(amostra['_cpu'], 2),
                    'ram_pct': round(amostra['_ram'], 2),
                    'disco_pct': round(amostra['_disco'], 2),
                    'download_mbps': round(down_m, 2) if down_m is not None else None,
                    'upload_mbps': round(up_m, 2) if up_m is not None else None,
                })

            noc_servidores.append({
                'id_servidor': id_servidor,
                'nome': ultima.get('username') or f'Servidor {id_servidor}',
                'ultima_coleta': ultima['_data'].astimezone(fuso).isoformat(),
                'atraso_segundos': max(0, int(round(atraso))),
                'sem_telemetria': atraso > cfg['offline_timeout'],
                'metricas_atuais': {
                    'cpu_pct': round(ultima['_cpu'], 2),
                    'ram_pct': round(ultima['_ram'], 2),
                    'disco_pct': round(ultima['_disco'], 2),
                    'download_mbps': round(down, 2) if down is not None else None,
                    'upload_mbps': round(up, 2) if up is not None else None,
                },
                'series_24h': series_24h_noc,
                'processos': processos_por_servidor.get((id_empresa, id_servidor), []),
            })

        documento_noc = {
            'id_empresa': empresa,
            'gerado_em': instante_atual.astimezone(fuso).isoformat(),
            'offline_timeout_segundos': cfg['offline_timeout'],
            'servidores': sorted(noc_servidores, key=lambda x: x['id_servidor']),
        }
        chave_noc = f"{cfg['gold']}empresa-{empresa}/noc.json"
        s3.put_object(
            Bucket=bucket,
            Key=chave_noc,
            Body=json.dumps(documento_noc, ensure_ascii=False, indent=2, allow_nan=False),
            ContentType='application/json'
        )
        print(f'Gerado: {chave_noc} ({len(noc_servidores)} servidores)')

    print('Silver -> Gold concluído.')


def lambda_handler(evento, contexto):
    """Ponto de entrada compatível com execução na AWS Lambda."""
    run()
    return {'etapa': 'silver2gold', 'status': 'concluido'}


if __name__ == '__main__':
    run()
