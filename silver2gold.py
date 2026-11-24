import csv
import io
import json
import os
from datetime import datetime, timezone
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

# ---------------- JSON SARAH: últimas capturas de cada servidor ----------------
CAPTURAS_SARAH = int(os.getenv('CAPTURAS_SARAH', '10'))
BANDA_MBPS = float(os.getenv('LARGURA_BANDA_MBPS', '1000'))         # capacidade da rede, base do cálculo de %
REDE_SARAH_TIMEOUT = int(os.getenv('REDE_SARAH_MAX_INTERVAL_SECONDS', '180'))  # intervalo máximo entre duas leituras


def rede_percentual(atual, anterior):
    """Usa a diferença dos contadores de bytes entre duas capturas para calcular % de uso da banda."""
    try:
        segundos = (atual['_data'] - anterior['_data']).total_seconds()
        enviados = int(atual['bytes_enviados']) - int(anterior['bytes_enviados'])
        recebidos = int(atual['bytes_recebidos']) - int(anterior['bytes_recebidos'])
    except (KeyError, ValueError, TypeError):
        return None
    # contador zerado (reinício da máquina) ou intervalo grande demais: não dá para confiar
    if not (0 < segundos <= REDE_SARAH_TIMEOUT) or enviados < 0 or recebidos < 0:
        return None
    mbps = (enviados + recebidos) * 8 / segundos / 1_000_000
    return round(min(100.0, mbps / BANDA_MBPS * 100), 1)


CAMPOS = ('cpu', 'ram', 'disco', 'rede')

for (id_empresa, id_servidor), amostras in servidores.items():
    lista = sorted(amostras.values(), key=lambda x: x['_data'])

    # pega capturas extras para calcular a rede e a variação da primeira captura do lote
    recorte = lista[-(CAPTURAS_SARAH + 2):]

    pontos = []
    for i, amostra in enumerate(recorte):
        anterior = recorte[i - 1] if i > 0 else None
        pontos.append({
            'dataHora': amostra['_data'].astimezone(FUSO_LOCAL).strftime('%Y-%m-%d %H:%M:%S'),
            'cpu': amostra['_cpu'],
            'ram': amostra['_ram'],
            'disco': amostra['_disco'],
            'rede': rede_percentual(amostra, anterior) if anterior else None,
        })

    capturas = pontos[-CAPTURAS_SARAH:] if CAPTURAS_SARAH else pontos

    # variação em relação à captura anterior, inclusive a última captura do lote anterior
    for i, captura in enumerate(capturas):
        indice_anterior = len(pontos) - len(capturas) + i - 1
        anterior = pontos[indice_anterior] if indice_anterior >= 0 else None
        captura['variacao'] = None
        if anterior is not None:
            captura['variacao'] = {
                c: (round(captura[c] - anterior[c], 1)
                    if captura[c] is not None and anterior[c] is not None else None)
                for c in CAMPOS
            }

    medias = {}
    for campo in CAMPOS:
        valores = [c[campo] for c in capturas if c[campo] is not None]
        medias[campo] = round(sum(valores) / len(valores), 1) if valores else None

    documento_sarah = {
        'id_empresa': id_empresa,
        'id_servidor': id_servidor,
        'nome_servidor': lista[-1].get('username') or f'Servidor {id_servidor}',
        'lote': capturas[-1]['dataHora'],
        'gerado_em': agora.isoformat(),
        'medias': medias,
        'capturas': capturas,
    }

    data_arquivo = capturas[0]['dataHora'][:10]
    chave_sarah = f'{GOLD}empresa_id={id_empresa}/servidor_id={id_servidor}/data={data_arquivo}/dashboard_sarah.json'
    s3.put_object(
        Bucket=BUCKET,
        Key=chave_sarah,
        Body=json.dumps(documento_sarah, ensure_ascii=False, indent=2),
        ContentType='application/json'
    )
    print(f'Gerado: {chave_sarah} ({len(capturas)} capturas)')

print('Silver -> Gold concluído.')
