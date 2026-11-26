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
# credenciais pela cadeia padrão (env vars locais, perfil ou IAM Role na EC2/Lambda)
regiao = os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION')
s3 = boto3.client('s3', region_name=regiao) if regiao else boto3.client('s3')

IDS_DADOS = re.compile(r'empresa-(\d+)_servidor-(\d+)_dados\.csv$')
IDS_PROCESSOS = re.compile(r'empresa-(\d+)_servidor-(\d+)_processos\.csv$')


def escrever_silver(destino, leitor, linhas):
    saida = io.StringIO()
    escritor = csv.DictWriter(saida, fieldnames=leitor.fieldnames, lineterminator='\n')
    escritor.writeheader()
    escritor.writerows(linhas)
    s3.put_object(Bucket=BUCKET, Key=destino, Body=saida.getvalue(), ContentType='text/csv')
    print(f'{destino.rsplit("/", 1)[-1]} -> {destino}')


def lote_para_data(nome):
    """O dia da partição vem do YYYYMMDD do lote no nome do arquivo.

    O lote é gerado em America/Sao_Paulo, então o "Z" no nome é enganoso.
    Um lote que cruza meia-noite continua gerando dois arquivos com o mesmo lote.
    """
    try:
        return datetime.strptime(nome[:8], '%Y%m%d').date().isoformat()
    except (ValueError, TypeError):
        return None


def run():
    if not BUCKET:
        raise RuntimeError('Defina S3_BUCKET no ambiente ou no arquivo .env.')

    for pagina in s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=BRONZE):
        for objeto in pagina.get('Contents', []):
            origem = objeto['Key']
            nome = origem.rsplit('/', 1)[-1]

            # ------------------------------------------------------------------
            # Processos: 02-silver/processos/empresa_id=.../servidor_id=.../data=...
            # ------------------------------------------------------------------
            if nome.endswith('_processos.csv'):
                ids = IDS_PROCESSOS.search(nome)
                if not ids:
                    print(f'Ignorado (IDs ausentes no nome): {origem}')
                    continue

                dia = lote_para_data(nome)
                if not dia:
                    print(f'Ignorado (lote inválido no nome): {origem}')
                    continue

                try:
                    empresa = int(ids.group(1))
                    servidor = int(ids.group(2))
                except (ValueError, TypeError):
                    print(f'Ignorado (IDs inválidos no nome): {origem}')
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

            dia = lote_para_data(nome)
            if not dia:
                print(f'Ignorado (lote inválido no nome): {origem}')
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
                    if ids and (empresa, servidor) != (int(ids.group(1)), int(ids.group(2))):
                        continue
                    if not linha['data_hora'] or not linha['cpu'] or not linha['ram'] or not linha['disco']:
                        continue
                    chave = (empresa, servidor, linha['data_hora'])
                    limpas[chave] = linha
                except (KeyError, ValueError, TypeError):
                    continue

            if not limpas:
                continue

            # Todas as linhas deste lote vão para a partição do dia do lote,
            # mesmo quando a coleta cruza meia-noite.
            por_servidor = {}
            for (empresa, servidor, _horario), linha in limpas.items():
                por_servidor.setdefault((empresa, servidor), []).append(linha)

            for (empresa, servidor), registros in por_servidor.items():
                registros.sort(key=lambda item: item['data_hora'])
                destino = (
                    f'{SILVER}metricas/empresa_id={empresa}/servidor_id={servidor}/'
                    f'data={dia}/{nome}'
                )
                escrever_silver(destino, leitor, registros)

    print('Bronze -> Silver concluído.')


def lambda_handler(evento, contexto):
    run()
    return {'etapa': 'bronze2silver', 'status': 'concluido'}


if __name__ == '__main__':
    run()
