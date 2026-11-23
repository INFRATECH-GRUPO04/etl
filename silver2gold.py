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
GOLD_BUCKET = os.getenv('S3_GOLD_BUCKET') or BUCKET
OFFLINE_TIMEOUT = int(os.getenv('OFFLINE_TIMEOUT_SECONDS', '900'))
FREQUENCIA = int(os.getenv('FREQUENCIA_ESPERADA_SEGUNDOS', '60'))
JANELAS = {'24h': 86400, '7d': 7 * 86400, '30d': 30 * 86400}
FUSO_LOCAL = ZoneInfo(os.getenv('TIMEZONE', 'America/Sao_Paulo'))
DIAS_SEMANA = ('Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom')

s3 = boto3.client('s3')
servidores = defaultdict(dict)


def disponibilidade(lista, inicio, fim):
    if not lista:
        return None
    recebidas = sum(1 for a in lista if inicio <= a['_data'] < fim)
    esperadas = (fim - inicio).total_seconds() / FREQUENCIA
    return round(min(100, recebidas / esperadas * 100), 1) if esperadas > 0 else None


def serie_frota(listas, segundos, passos, rotulo):
    def serie(fim):
        inicio = fim - timedelta(seconds=segundos * passos)
        rotulos, valores = [], []
        for i in range(passos):
            inicio_bucket = inicio + timedelta(seconds=segundos * i)
            fim_bucket = inicio_bucket + timedelta(seconds=segundos)
            pontos = [disponibilidade(lista, inicio_bucket, fim_bucket) for lista in listas]
            pontos = [v for v in pontos if v is not None]
            rotulos.append(rotulo(inicio_bucket))
            valores.append(round(sum(pontos) / len(pontos), 1) if pontos else None)
        validos = [v for v in valores if v is not None]
        media = round(sum(validos) / len(validos), 1) if validos else None
        return rotulos, valores, media

    rotulos, valores, media = serie(agora)
    _, _, anterior = serie(agora - timedelta(seconds=segundos * passos))
    return {'rotulos': rotulos, 'valores': valores, 'media': media, 'anterior': anterior}


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
empresas = sorted({empresa for empresa, servidor in servidores})

rotulos = {
    '24h': lambda ini: ini.astimezone(FUSO_LOCAL).strftime('%Hh'),
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
            'nome': None,
            'cidade': None,
            'conexao': conexao,
            'ultima_leitura': ultima['_data'].isoformat(),
            'tempo_min': None,
            'situacao': None,
            'inc7d': None,
            'atual': {
                'cpu': ultima['_cpu'],
                'ram': ultima['_ram'],
                'disco': ultima['_disco'],
            },
            'disponibilidade_pct': {
                '24h': disponibilidade(lista, agora - timedelta(seconds=JANELAS['24h']), agora),
            },
        })

    servidores_empresa.sort(key=lambda s: s['id_servidor'])
    documento = {
        'versao': 1,
        'id_empresa': empresa,
        'gerado_em': agora.isoformat(),
        'ambiente': None,
        'servidores': servidores_empresa,
        'disponibilidade_frota': {
            '24h': serie_frota(listas, JANELAS['24h'] / 24, 24, rotulos['24h']),
            '7d': serie_frota(listas, 86400, 7, rotulos['7d']),
            '30d': serie_frota(listas, 86400, 30, rotulos['30d']),
        },
        'incidentes': {
            'abertos': [],
            'por_dia': {
                '7d': {'rotulos': [], 'valores': [], 'anterior': None},
                '30d': {'rotulos': [], 'valores': [], 'anterior': None},
            },
            'mttr': {'minutos': None, 'periodo': '(últimos 30d)'},
        },
        'relatorio_ia': {
            'gerado_em': None,
            'resumo': '',
            'pontos': [],
            'recomendacoes': [],
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
