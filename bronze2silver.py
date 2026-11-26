import csv
import io
import os
import re
from datetime import datetime
import boto3

# Lê as configurações do .env, sem instalar outra biblioteca.
if os.path.exists('.env'):
    for linha in open('.env', encoding='utf-8'):
        if '=' in linha and not linha.lstrip().startswith('#'):
            chave, valor = linha.strip().split('=', 1)
            os.environ.setdefault(chave, valor.strip().strip('"').strip("'"))

BUCKET = os.getenv('S3_BUCKET')
BRONZE = os.getenv('S3_BRONZE_PREFIX', '01-bronze/').strip('/') + '/'
SILVER = os.getenv('S3_SILVER_PREFIX', '02-silver/').strip('/') + '/'
s3 = boto3.client('s3')  # Usa a IAM Role da EC2

IDS_DADOS = re.compile(r'empresa-(\d+)_servidor-(\d+)_dados\.csv$')
IDS_PROCESSOS = re.compile(r'empresa-(\d+)_servidor-(\d+)_processos\.csv$')


def escrever_silver(destino, leitor, linhas):
    saida = io.StringIO()
    escritor = csv.DictWriter(saida, fieldnames=leitor.fieldnames, lineterminator='\n')
    escritor.writeheader()
    escritor.writerows(linhas)
    s3.put_object(Bucket=BUCKET, Key=destino, Body=saida.getvalue(), ContentType='text/csv')
    print(f'{destino.rsplit("/", 1)[-1]} -> {destino}')


for pagina in s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=BRONZE):
    for objeto in pagina.get('Contents', []):
        origem = objeto['Key']
        nome = origem.rsplit('/', 1)[-1]

        # ------------------------------------------------------------------
        # Processos: 02-silver/processos/empresa_id=.../servidor_id=.../data=...
        # O lote do nome é gerado em America/Sao_Paulo, então o YYYYMMDD dele
        # é usado como dia da partição (o "Z" do nome é enganoso).
        # ------------------------------------------------------------------
        if nome.endswith('_processos.csv'):
            ids = IDS_PROCESSOS.search(nome)
            if not ids:
                print(f'Ignorado (IDs ausentes no nome): {origem}')
                continue
            try:
                empresa = int(ids.group(1))
                servidor = int(ids.group(2))
                dia = datetime.strptime(nome[:8], '%Y%m%d').date().isoformat()
            except (ValueError, TypeError):
                print(f'Ignorado (lote inválido no nome): {origem}')
                continue

            conteudo = s3.get_object(Bucket=BUCKET, Key=origem)['Body'].read().decode('utf-8-sig')
            leitor = csv.DictReader(io.StringIO(conteudo))
            if 'PID' not in (leitor.fieldnames or []):
                print(f'Ignorado (cabeçalho sem PID): {origem}')
                continue

            linhas = []
            vistos = set()
            for linha in leitor:
                try:
                    pid = int(linha['PID'])
                except (KeyError, ValueError, TypeError):
                    continue
                nome_processo = (linha.get('Nome') or '').strip()
                usuario = (linha.get('Username') or '').strip()
                if not nome_processo or (pid, nome_processo) in vistos:
                    continue
                vistos.add((pid, nome_processo))
                linhas.append(linha)

            if not linhas:
                continue

            destino = (
                f'{SILVER}processos/empresa_id={empresa}/servidor_id={servidor}/'
                f'data={dia}/{nome}'
            )
            escrever_silver(destino, leitor, linhas)
            continue

        # ------------------------------------------------------------------
        # Métricas: contrato da silver inalterado (apenas _dados.csv).
        # ------------------------------------------------------------------
        if not nome.endswith('_dados.csv'):
            continue

        conteudo = s3.get_object(Bucket=BUCKET, Key=origem)['Body'].read().decode('utf-8-sig')
        leitor = csv.DictReader(io.StringIO(conteudo))
        linhas = list(leitor)
        if not linhas:
            continue

        # Confere os IDs do nome com os IDs que estão dentro do CSV.
        ids = IDS_DADOS.search(nome)
        limpas = {}
        for linha in linhas:
            try:
                empresa = int(linha['id_empresa'])
                servidor = int(linha['id_servidor'])
                data = linha['data_hora'][:10]
                if ids and (empresa, servidor) != (int(ids.group(1)), int(ids.group(2))):
                    continue
                if not linha['data_hora'] or not linha['cpu'] or not linha['ram'] or not linha['disco']:
                    continue
                chave = (empresa, servidor, linha['data_hora'])
                limpas[chave] = linha
            except (KeyError, ValueError, TypeError):
                continue

        # Se um arquivo tiver coletas de dias diferentes, separa por dia.
        por_dia = {}
        for (empresa, servidor, horario), linha in limpas.items():
            dia = horario[:10]
            por_dia.setdefault((empresa, servidor, dia), []).append(linha)

        for (empresa, servidor, dia), registros in por_dia.items():
            destino = f'{SILVER}metricas/empresa_id={empresa}/servidor_id={servidor}/data={dia}/{nome}'
            escrever_silver(destino, leitor, registros)

print('Bronze -> Silver concluído.')
