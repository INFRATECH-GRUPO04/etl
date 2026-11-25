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
                linha['_arquivo'] = objeto['Key']          # <- NOVA
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
ARQUIVO_GOLD = os.getenv('GOLD_ARQUIVO', 'perfeito-sarah.json')


def media(valores):
    validos = [v for v in valores if v is not None]
    return round(sum(validos) / len(validos), 1) if validos else None


def medias_de(capturas):
    return {c: media([x[c] for x in capturas]) for c in CAMPOS}


for (id_empresa, id_servidor), amostras in servidores.items():
    lista = sorted(amostras.values(), key=lambda x: x['_data'])

    # 1) todos os pontos, com rede e variação calculadas na sequência completa
    #    (a 1ª captura de cada CSV usa a última do CSV anterior como referência)
    pontos, origens = [], []
    for i, amostra in enumerate(lista):
        ponto = {
            'dataHora': amostra['_data'].astimezone(FUSO_LOCAL).strftime('%Y-%m-%d %H:%M:%S'),
            'cpu': amostra['_cpu'],
            'ram': amostra['_ram'],
            'disco': amostra['_disco'],
            'rede': rede_percentual(amostra, lista[i - 1]) if i > 0 else None,
            'variacao': None,
        }
        if pontos:
            ant = pontos[-1]
            ponto['variacao'] = {
                c: (round(ponto[c] - ant[c], 1)
                    if ponto[c] is not None and ant[c] is not None else None)
                for c in CAMPOS
            }
        pontos.append(ponto)
        origens.append(amostra['_arquivo'])

    # 2) agrupa por dia local e, dentro do dia, por CSV de origem
    por_dia = defaultdict(lambda: defaultdict(list))
    for ponto, arquivo in zip(pontos, origens):
        por_dia[ponto['dataHora'][:10]][arquivo].append(ponto)

    nome = lista[-1].get('username') or f'Servidor {id_servidor}'

    # 3) um JSON por dia, sobrescrito inteiro a cada execução (idempotente)
    for dia, blocos in por_dia.items():
        horarios = []
        for arquivo, caps in sorted(blocos.items(), key=lambda kv: kv[1][0]['dataHora']):
            horarios.append({
                'lote': caps[-1]['dataHora'],
                'arquivo': arquivo.split('/')[-1],
                'medias': medias_de(caps),
                'capturas': caps,
            })
        todas = [c for h in horarios for c in h['capturas']]

        documento = {
            'id_empresa': id_empresa,
            'id_servidor': id_servidor,
            'nome_servidor': nome,
            'data': dia,
            'gerado_em': agora.isoformat(),
            'medias': medias_de(todas),
            'horarios': horarios,
        }

        chave = f'{GOLD}metricas/empresa_id={id_empresa}/servidor_id={id_servidor}/data={dia}/{ARQUIVO_GOLD}'
        s3.put_object(
            Bucket=BUCKET,
            Key=chave,
            Body=json.dumps(documento, ensure_ascii=False),
            ContentType='application/json'
        )
        print(f'Gerado: {chave} ({len(horarios)} horários, {len(todas)} capturas)')

print('Silver -> Gold concluído.')