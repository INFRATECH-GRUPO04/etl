import csv
import io
import json
import os
import re
from datetime import datetime, timedelta, time, timezone
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
GOLD_BUCKET = os.getenv('S3_GOLD_BUCKET') or BUCKET
OFFLINE_TIMEOUT = int(os.getenv('OFFLINE_TIMEOUT_SECONDS', '900'))
FREQUENCIA = int(os.getenv('FREQUENCIA_ESPERADA_SEGUNDOS', '60'))
FUSO_LOCAL = ZoneInfo(os.getenv('TIMEZONE', 'America/Sao_Paulo'))
DIAS_HISTORICO = 40
DIAS_SERIE = 30

# Partição da silver: .../data=AAAA-MM-DD/...
DATA_NA_CHAVE = re.compile(r'(?:^|/)data=(\d{4}-\d{2}-\d{2})(?:/|$)')

s3 = boto3.client('s3')
servidores = defaultdict(dict)


def ponto_servidor(lista, dia, agora):
    """Um dia de calendário em FUSO_LOCAL. O dia de hoje vai só até `agora`."""
    inicio = datetime.combine(dia, time.min, tzinfo=FUSO_LOCAL).astimezone(timezone.utc)
    if dia == agora.astimezone(FUSO_LOCAL).date():
        fim = agora
    else:
        fim = datetime.combine(dia + timedelta(days=1), time.min, tzinfo=FUSO_LOCAL).astimezone(timezone.utc)

    # Antes da primeira captura do servidor o dia não tem ponto (None, não 0).
    if not lista or fim <= lista[0]['_data']:
        return None

    inicio_efetivo = max(inicio, lista[0]['_data'])
    esperadas = (fim - inicio_efetivo).total_seconds() / FREQUENCIA
    if esperadas <= 0:
        return None
    recebidas = sum(1 for a in lista if inicio_efetivo <= a['_data'] < fim)
    return min(100, recebidas / esperadas * 100), recebidas, esperadas


def serie_frota(listas, agora):
    """30 dias de calendário; a frota é ponderada por recebidas/esperadas."""
    hoje = agora.astimezone(FUSO_LOCAL).date()
    rotulos, valores = [], []
    for passo in range(DIAS_SERIE - 1, -1, -1):
        dia = hoje - timedelta(days=passo)
        soma_recebidas = soma_esperadas = 0
        for lista in listas:
            ponto = ponto_servidor(lista, dia, agora)
            if ponto is None:
                continue
            _, recebidas, esperadas = ponto
            soma_recebidas += recebidas
            soma_esperadas += esperadas
        valor = min(100, soma_recebidas / soma_esperadas * 100) if soma_esperadas else None
        rotulos.append(dia.strftime('%d/%m'))
        valores.append(round(valor, 1) if valor is not None else None)
    return {'rotulos': rotulos, 'valores': valores}


# O filtro de 40 dias é aplicado pela chave antes de baixar o objeto.
agora = datetime.now(timezone.utc)
limite = agora.astimezone(FUSO_LOCAL).date() - timedelta(days=DIAS_HISTORICO)
processados = 0
ignorados = 0

# Lê as coletas da Silver; horários sem fuso são considerados locais.
for pagina in s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=SILVER + 'metricas/'):
    for objeto in pagina.get('Contents', []):
        chave = objeto['Key']
        if not chave.endswith('_dados.csv'):
            continue

        particao = DATA_NA_CHAVE.search(chave)
        if particao:
            try:
                data_particao = datetime.strptime(particao.group(1), '%Y-%m-%d').date()
            except ValueError:
                data_particao = None
            if data_particao is not None and data_particao < limite:
                ignorados += 1
                continue

        processados += 1
        texto = s3.get_object(Bucket=BUCKET, Key=chave)['Body'].read().decode('utf-8-sig')
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

print(f'Objetos silver: {processados} processados, {ignorados} ignorados por terem mais de {DIAS_HISTORICO} dias.')

empresas = sorted({empresa for empresa, servidor in servidores})

for empresa in empresas:
    servidores_empresa = []
    listas = []

    for (id_empresa, id_servidor), amostras in servidores.items():
        if id_empresa != empresa:
            continue
        lista = sorted(amostras.values(), key=lambda x: x['_data'])
        listas.append(lista)
        ultima = lista[-1]
        atraso = (agora - ultima['_data']).total_seconds()
        conexao = 'offline' if atraso > OFFLINE_TIMEOUT else 'online'

        servidores_empresa.append({
            'id_servidor': id_servidor,
            'conexao': conexao,
            'ultima_leitura': ultima['_data'].astimezone(FUSO_LOCAL).isoformat(),
            'atual': {
                'cpu': ultima['_cpu'],
                'ram': ultima['_ram'],
                'disco': ultima['_disco'],
            },
        })

    servidores_empresa.sort(key=lambda s: s['id_servidor'])
    frota = serie_frota(listas, agora)
    documento = {
        'versao': 2,
        'id_empresa': empresa,
        'gerado_em': agora.astimezone(FUSO_LOCAL).isoformat(),
        'servidores': servidores_empresa,
        'disponibilidade_frota': {
            '7d': {
                'rotulos': frota['rotulos'][-7:],
                'valores': frota['valores'][-7:],
            },
            '30d': frota,
        },
    }

    chave = f'{GOLD}empresa-{empresa}/dash-geral.json'
    s3.put_object(
        Bucket=GOLD_BUCKET,
        Key=chave,
        Body=json.dumps(documento, ensure_ascii=False, indent=2),
        ContentType='application/json'
    )
    print(f'Gerado: {chave} ({len(servidores_empresa)} servidores)')

print('Silver -> Gold concluído.')
