import csv
import io
import json
import os
import re
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
GOLD_BUCKET = os.getenv('S3_GOLD_BUCKET') or BUCKET
OFFLINE_TIMEOUT = int(os.getenv('OFFLINE_TIMEOUT_SECONDS', '900'))
FREQUENCIA = int(os.getenv('FREQUENCIA_ESPERADA_SEGUNDOS', '60'))
FUSO_LOCAL = ZoneInfo(os.getenv('TIMEZONE', 'America/Sao_Paulo'))
DIAS_SEMANA = ('Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom')
DIAS_HISTORICO = 40

# Partição da silver: .../data=AAAA-MM-DD/...
DATA_NA_CHAVE = re.compile(r'(?:^|/)data=(\d{4}-\d{2}-\d{2})(?:/|$)')

s3 = boto3.client('s3')
servidores = defaultdict(dict)


def disponibilidade(lista, inicio, fim):
    if not lista:
        return None
    recebidas = sum(1 for a in lista if inicio <= a['_data'] < fim)
    esperadas = (fim - inicio).total_seconds() / FREQUENCIA
    return round(min(100, recebidas / esperadas * 100), 1) if esperadas > 0 else None


def serie_frota(listas, segundos, passos, rotulo):
    inicio = agora - timedelta(seconds=segundos * passos)
    rotulos, valores = [], []
    for i in range(passos):
        inicio_bucket = inicio + timedelta(seconds=segundos * i)
        fim_bucket = inicio_bucket + timedelta(seconds=segundos)
        pontos = [disponibilidade(lista, inicio_bucket, fim_bucket) for lista in listas]
        pontos = [v for v in pontos if v is not None]
        rotulos.append(rotulo(inicio_bucket))
        valores.append(round(sum(pontos) / len(pontos), 1) if pontos else None)
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

rotulos = {
    '7d': lambda ini: DIAS_SEMANA[ini.astimezone(FUSO_LOCAL).weekday()],
    '30d': lambda ini: ini.astimezone(FUSO_LOCAL).strftime('%d/%m'),
}

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
    documento = {
        'versao': 2,
        'id_empresa': empresa,
        'gerado_em': agora.astimezone(FUSO_LOCAL).isoformat(),
        'servidores': servidores_empresa,
        'disponibilidade_frota': {
            '7d': serie_frota(listas, 86400, 7, rotulos['7d']),
            '30d': serie_frota(listas, 86400, 30, rotulos['30d']),
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
