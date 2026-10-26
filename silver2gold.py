import csv
import io
import os
from datetime import datetime, timedelta
from collections import defaultdict
import boto3

if os.path.exists('.env'):
    for linha in open('.env', encoding='utf-8'):
        if '=' in linha and not linha.lstrip().startswith('#'):
            chave, valor = linha.strip().split('=', 1)
            os.environ.setdefault(chave, valor.strip('"\''))

BUCKET = os.getenv('S3_BUCKET')
SILVER = os.getenv('S3_SILVER_PREFIX', '02-silver/').strip('/') + '/'
GOLD = os.getenv('S3_GOLD_PREFIX', '03-gold/').strip('/') + '/'
TIMEOUT = int(os.getenv('OFFLINE_TIMEOUT_SECONDS', '30'))
LIMITE_CPU = float(os.getenv('LIMITE_CPU_PCT', '80'))
LIMITE_RAM = float(os.getenv('LIMITE_RAM_PCT', '80'))
LIMITE_DISCO = float(os.getenv('LIMITE_DISCO_PCT', '80'))
s3 = boto3.client('s3')
servidores = defaultdict(dict)

# Junta as amostras da Silver. A chave evita repetir uma coleta.
for pagina in s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=SILVER + 'metricas/'):
    for objeto in pagina.get('Contents', []):
        if not objeto['Key'].endswith('_dados.csv'):
            continue
        texto = s3.get_object(Bucket=BUCKET, Key=objeto['Key'])['Body'].read().decode('utf-8-sig')
        for linha in csv.DictReader(io.StringIO(texto)):
            try:
                empresa = int(linha['id_empresa'])
                servidor = int(linha['id_servidor'])
                data = datetime.fromisoformat(linha['data_hora'].replace('Z', ''))
                linha['_data'] = data
                linha['_cpu'] = float(linha['cpu'])
                linha['_ram'] = float(linha['ram'])
                linha['_disco'] = float(linha['disco'])
                servidores[(empresa, servidor)][data] = linha
            except (KeyError, ValueError, TypeError):
                continue

agora = datetime.utcnow()
inicio = agora - timedelta(hours=24)

# Escreve um CSV no S3.
def salvar(chave, colunas, linhas):
    arquivo = io.StringIO()
    escritor = csv.DictWriter(arquivo, fieldnames=colunas, extrasaction='ignore', lineterminator='\n')
    escritor.writeheader()
    escritor.writerows(linhas)
    s3.put_object(Bucket=BUCKET, Key=chave, Body=arquivo.getvalue(), ContentType='text/csv')

empresas = set(empresa for empresa, servidor in servidores)
for empresa in empresas:
    atuais, historico, alertas = [], [], []

    for (id_empresa, id_servidor), amostras in servidores.items():
        if id_empresa != empresa:
            continue
        lista = sorted(amostras.values(), key=lambda x: x['_data'])

        # Calcula upload/download usando a diferença dos contadores de bytes.
        anterior = None
        for amostra in lista:
            amostra['upload_mbps'] = ''
            amostra['download_mbps'] = ''
            if anterior:
                segundos = (amostra['_data'] - anterior['_data']).total_seconds()
                try:
                    enviados = int(amostra['bytes_enviados']) - int(anterior['bytes_enviados'])
                    recebidos = int(amostra['bytes_recebidos']) - int(anterior['bytes_recebidos'])
                    if 0 < segundos <= TIMEOUT and enviados >= 0 and recebidos >= 0:
                        amostra['upload_mbps'] = round(enviados * 8 / segundos / 1_000_000, 2)
                        amostra['download_mbps'] = round(recebidos * 8 / segundos / 1_000_000, 2)
                except (KeyError, ValueError, TypeError):
                    pass
            anterior = amostra

        ultima = lista[-1]
        atraso = (agora - ultima['_data']).total_seconds()
        if atraso > TIMEOUT:
            status = 'offline'
        elif ultima['_cpu'] >= LIMITE_CPU or ultima['_ram'] >= LIMITE_RAM or ultima['_disco'] >= LIMITE_DISCO:
            status = 'atencao'
        else:
            status = 'online'

        nome = ultima.get('username') or f'servidor-{id_servidor}'
        atuais.append({
            'id_empresa': empresa, 'id_servidor': id_servidor, 'nome_servidor': nome,
            'ultima_coleta': ultima['data_hora'], 'cpu_pct': ultima['_cpu'],
            'ram_pct': ultima['_ram'], 'disco_pct': ultima['_disco'],
            'upload_mbps': ultima['upload_mbps'], 'download_mbps': ultima['download_mbps'],
            'status': status,
        })

        for amostra in lista:
            if inicio <= amostra['_data'] <= agora:
                historico.append({
                    'data_hora': amostra['data_hora'], 'id_empresa': empresa,
                    'id_servidor': id_servidor, 'nome_servidor': amostra.get('username', nome),
                    'cpu_pct': amostra['_cpu'], 'ram_pct': amostra['_ram'], 'disco_pct': amostra['_disco'],
                    'upload_mbps': amostra['upload_mbps'], 'download_mbps': amostra['download_mbps'],
                })

        if status == 'offline':
            alertas.append({'id_empresa': empresa, 'id_servidor': id_servidor, 'nome_servidor': nome,
                            'tipo': 'offline', 'severidade': 'critico', 'mensagem': f'Sem coleta há {int(atraso)} segundos'})
        else:
            for campo, valor, limite in [('cpu_alta', ultima['_cpu'], LIMITE_CPU),
                                         ('ram_alta', ultima['_ram'], LIMITE_RAM),
                                         ('disco_alto', ultima['_disco'], LIMITE_DISCO)]:
                if valor >= limite:
                    alertas.append({'id_empresa': empresa, 'id_servidor': id_servidor, 'nome_servidor': nome,
                                    'tipo': campo, 'severidade': 'atencao', 'mensagem': f'{campo}: {valor}% (limite {limite}%)'})

    prefixo = f'{GOLD}empresa_id={empresa}/'
    salvar(prefixo + 'servidores_atual.csv',
           ['id_empresa', 'id_servidor', 'nome_servidor', 'ultima_coleta', 'cpu_pct', 'ram_pct', 'disco_pct', 'upload_mbps', 'download_mbps', 'status'], atuais)
    salvar(prefixo + 'historico_24h.csv',
           ['data_hora', 'id_empresa', 'id_servidor', 'nome_servidor', 'cpu_pct', 'ram_pct', 'disco_pct', 'upload_mbps', 'download_mbps'], historico)
    salvar(prefixo + 'alertas_ativos.csv',
           ['id_empresa', 'id_servidor', 'nome_servidor', 'tipo', 'severidade', 'mensagem'], alertas)
    print(f'Empresa {empresa}: {len(atuais)} servidores, {len(historico)} amostras, {len(alertas)} alertas')

print('Silver -> Gold concluído.')
