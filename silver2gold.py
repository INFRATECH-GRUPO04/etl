import csv
import io
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def carregar_env(caminho='.env'):
    """Carrega variáveis de um arquivo .env local, se existir."""
    if not os.path.exists(caminho):
        return
    with open(caminho, encoding='utf-8') as arquivo:
        for linha in arquivo:
            if '=' in linha and not linha.lstrip().startswith('#'):
                chave, valor = linha.strip().split('=', 1)
                os.environ.setdefault(chave, valor.strip('"\''))


def carregar_config():
    """Lê variáveis de ambiente com os mesmos padrões das demais branches da equipe."""
    carregar_env()
    bucket = os.getenv('S3_BUCKET') or ''
    gold_bucket = os.getenv('S3_GOLD_BUCKET') or bucket
    return {
        'bucket': bucket,
        'gold_bucket': gold_bucket,
        'silver': os.getenv('S3_SILVER_PREFIX', '02-silver/').strip('/') + '/',
        'gold': os.getenv('S3_GOLD_PREFIX', '03-gold/').strip('/') + '/',
        'offline_timeout': int(os.getenv('OFFLINE_TIMEOUT_SECONDS', '900')),
        'janela_dias': int(os.getenv('PLANEJAMENTO_JANELA_DIAS', '30')),
        'fuso_local': ZoneInfo(os.getenv('TIMEZONE', 'America/Sao_Paulo')),
    }


def processar_csv_silver(texto, fuso_local, servidores):
    """Lê linhas de um CSV da camada Silver e deduplica por (empresa, servidor, timestamp UTC)."""
    for linha in csv.DictReader(io.StringIO(texto)):
        try:
            empresa = int(linha['id_empresa'])
            servidor = int(linha['id_servidor'])
            data = datetime.fromisoformat(linha['data_hora'].replace('Z', '+00:00'))
            if data.tzinfo is None:
                data = data.replace(tzinfo=fuso_local)
            data = data.astimezone(timezone.utc)
            cpu = float(linha['cpu'])
            ram = float(linha['ram'])
            disco = float(linha['disco'])
            if not (math.isfinite(cpu) and math.isfinite(ram) and math.isfinite(disco)):
                continue
            servidores[(empresa, servidor)][data] = {
                '_data': data,
                '_cpu': cpu,
                '_ram': ram,
                '_disco': disco,
            }
        except (KeyError, ValueError, TypeError):
            continue


def ler_amostras_silver(s3, cfg):
    """Lê todas as coletas da Silver no S3 (ou pasta local quando S3_BUCKET não está definido)."""
    servidores = defaultdict(dict)
    prefixo = cfg['silver'] + 'metricas/'

    if cfg['bucket']:
        for pagina in s3.get_paginator('list_objects_v2').paginate(Bucket=cfg['bucket'], Prefix=prefixo):
            for objeto in pagina.get('Contents', []):
                if not objeto['Key'].endswith('_dados.csv'):
                    continue
                texto = s3.get_object(Bucket=cfg['bucket'], Key=objeto['Key'])['Body'].read().decode('utf-8-sig')
                processar_csv_silver(texto, cfg['fuso_local'], servidores)
        return servidores

    if os.path.isdir(prefixo):
        for raiz, _, arquivos in os.walk(prefixo):
            for nome in sorted(arquivos):
                if not nome.endswith('_dados.csv'):
                    continue
                with open(os.path.join(raiz, nome), encoding='utf-8-sig') as arquivo:
                    processar_csv_silver(arquivo.read(), cfg['fuso_local'], servidores)
    return servidores


def resumir_servidor(id_servidor, lista, agora, inicio_janela, offline_timeout):
    """Resume uso médio, pico e médias auxiliares na janela de planejamento para um servidor."""
    ultima = lista[-1]
    atraso = (agora - ultima['_data']).total_seconds()
    conexao = 'offline' if atraso > offline_timeout else 'online'

    amostras_janela = [a for a in lista if inicio_janela <= a['_data'] <= agora]
    qtd = len(amostras_janela)

    if qtd > 0:
        uso_medio = round(sum(a['_cpu'] for a in amostras_janela) / qtd, 1)
        pico = round(max(a['_cpu'] for a in amostras_janela), 1)
        ram_media = round(sum(a['_ram'] for a in amostras_janela) / qtd, 1)
        disco_medio = round(sum(a['_disco'] for a in amostras_janela) / qtd, 1)
    else:
        uso_medio = None
        pico = None
        ram_media = None
        disco_medio = None

    return {
        'id_servidor': id_servidor,
        'conexao': conexao,
        'ultima_leitura': ultima['_data'].isoformat(),
        'amostras_30d': qtd,
        'uso_medio_30d': uso_medio,
        'pico_30d': pico,
        'ram_media_30d': ram_media,
        'disco_medio_30d': disco_medio,
    }


def gerar_documentos_planejamento(servidores, agora, cfg):
    """Monta um documento Gold planejamento.json por empresa."""
    inicio_janela = agora - timedelta(days=cfg['janela_dias'])
    empresas = sorted({empresa for empresa, _ in servidores})
    documentos = {}

    for empresa in empresas:
        lista_servidores = []
        for (id_empresa, id_servidor), amostras in servidores.items():
            if id_empresa != empresa:
                continue
            ordenadas = sorted(amostras.values(), key=lambda item: item['_data'])
            lista_servidores.append(
                resumir_servidor(id_servidor, ordenadas, agora, inicio_janela, cfg['offline_timeout'])
            )

        lista_servidores.sort(key=lambda item: item['id_servidor'])
        documentos[empresa] = {
            'versao': 1,
            'id_empresa': empresa,
            'gerado_em': agora.isoformat(),
            'janela_dias': cfg['janela_dias'],
            'servidores': lista_servidores,
        }

    return documentos


def gravar_gold(s3, cfg, empresa, documento):
    """Grava 03-gold/empresa-{id}/planejamento.json no S3 ou localmente em modo dev."""
    chave = f"{cfg['gold']}empresa-{empresa}/planejamento.json"
    corpo = json.dumps(documento, ensure_ascii=False, indent=2, allow_nan=False)

    if cfg['gold_bucket']:
        s3.put_object(
            Bucket=cfg['gold_bucket'],
            Key=chave,
            Body=corpo,
            ContentType='application/json',
        )
    else:
        os.makedirs(os.path.dirname(chave), exist_ok=True)
        with open(chave, 'w', encoding='utf-8') as arquivo:
            arquivo.write(corpo)

    print(f"Gerado: {chave} ({len(documento['servidores'])} servidores)")
    return chave


def main():
    import boto3

    cfg = carregar_config()
    s3 = boto3.client('s3') if cfg['bucket'] or cfg['gold_bucket'] else None
    servidores = ler_amostras_silver(s3, cfg)
    agora = datetime.now(timezone.utc)
    documentos = gerar_documentos_planejamento(servidores, agora, cfg)

    for empresa, documento in documentos.items():
        gravar_gold(s3, cfg, empresa, documento)

    print('Silver -> Gold (planejamento) concluído.')


if __name__ == '__main__':
    main()
