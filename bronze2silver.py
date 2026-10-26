import csv
import io
import os
import re
import boto3

# Lê as configurações do .env, sem instalar outra biblioteca.
if os.path.exists('.env'):
    for linha in open('.env', encoding='utf-8'):
        if '=' in linha and not linha.lstrip().startswith('#'):
            chave, valor = linha.strip().split('=', 1)
            os.environ.setdefault(chave, valor.strip('"\''))

BUCKET = os.getenv('S3_BUCKET')
BRONZE = os.getenv('S3_BRONZE_PREFIX', '01-bronze/').strip('/') + '/'
SILVER = os.getenv('S3_SILVER_PREFIX', '02-silver/').strip('/') + '/'
s3 = boto3.client('s3')  # Usa a IAM Role da EC2

for pagina in s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=BRONZE):
    for objeto in pagina.get('Contents', []):
        origem = objeto['Key']
        nome = origem.rsplit('/', 1)[-1]
        if not nome.endswith('_dados.csv'):
            continue

        conteudo = s3.get_object(Bucket=BUCKET, Key=origem)['Body'].read().decode('utf-8-sig')
        leitor = csv.DictReader(io.StringIO(conteudo))
        linhas = list(leitor)
        if not linhas:
            continue

        # Confere os IDs do nome com os IDs que estão dentro do CSV.
        ids = re.search(r'empresa-(\d+)_servidor-(\d+)_dados\.csv$', nome)
        limpas = {}
        for linha in linhas:
            try:
                empresa = int(linha['id_empresa'])
                servidor = int(linha['id_servidor'])
                data = linha['data_hora'][:10]
                if ids and (empresa, servidor) != (int(ids[1]), int(ids[2])):
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
            saida = io.StringIO()
            escritor = csv.DictWriter(saida, fieldnames=leitor.fieldnames, lineterminator='\n')
            escritor.writeheader()
            escritor.writerows(registros)
            s3.put_object(Bucket=BUCKET, Key=destino, Body=saida.getvalue(), ContentType='text/csv')
            print(f'{origem} -> {destino}')

print('Bronze -> Silver concluído.')
