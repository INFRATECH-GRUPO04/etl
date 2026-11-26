"""Silver -> Gold: gera os JSONs consumidos pelas dashboards do InfraTech.

Uma única leitura da camada Silver alimenta todas as saídas. A lógica do
`dash-geral.json` (contrato v2 homologado) tem precedência sobre as demais.
"""

import csv
import hashlib
import io
import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import boto3

# Partição da silver: .../data=AAAA-MM-DD/...
DATA_NA_CHAVE = re.compile(r'(?:^|/)data=(\d{4}-\d{2}-\d{2})(?:/|$)')
PROCESSO_NA_CHAVE = re.compile(
    r'empresa_id=(\d+)/servidor_id=(\d+)/data=(\d{4}-\d{2}-\d{2})/[^/]+_processos\.csv$'
)

CAMPOS_CAPTURA = [
    'timestamp', 'nome_servidor', 'cpu_pct', 'ram_pct', 'disco_pct',
    'upload_mbps', 'download_mbps', 'rede_mbps', 'bytes_enviados',
    'bytes_recebidos',
]


def carregar_env():
    """Lê o .env local sem dependência extra; na Lambda a IAM Role já basta."""
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.exists(caminho):
        caminho = '.env'
    if not os.path.exists(caminho):
        return
    with open(caminho, encoding='utf-8') as arquivo:
        for linha in arquivo:
            if '=' in linha and not linha.lstrip().startswith('#'):
                chave, valor = linha.strip().split('=', 1)
                os.environ.setdefault(chave, valor.strip().strip('"').strip("'"))


def carregar_config():
    """Configuração comum a todas as dashboards."""
    carregar_env()
    bucket = os.getenv('S3_BUCKET')
    if not bucket:
        raise RuntimeError('Defina S3_BUCKET no ambiente ou no arquivo .env.')

    return {
        'bucket': bucket,
        'silver': os.getenv('S3_SILVER_PREFIX', '02-silver/').strip('/') + '/',
        'gold': os.getenv('S3_GOLD_PREFIX', '03-gold/').strip('/') + '/',
        'fuso': ZoneInfo(os.getenv('TIMEZONE', 'America/Sao_Paulo')),
        'offline_timeout': int(os.getenv('OFFLINE_TIMEOUT_SECONDS', '2400')),
        'frequencia': int(os.getenv('FREQUENCIA_ESPERADA_SEGUNDOS', '60')),
        'dias_historico': 40,
        'dias_serie': 30,
        'limites': {
            campo: float(os.getenv(f'LIMITE_{campo.upper()}_PCT', '80'))
            for campo in ('cpu', 'ram', 'disco')
        },
        'rede_timeout': int(os.getenv('REDE_MAX_INTERVAL_SECONDS', '90')),
        'banda_mbps': float(os.getenv('LARGURA_BANDA_MBPS', '1000')),
        'janela_capacidade_dias': int(os.getenv('PLANEJAMENTO_JANELA_DIAS', '30')),
        'retencao_historico_dias': int(os.getenv('GOLD_RETENTION_DAYS', '180')),
    }


def obter_cliente_s3():
    regiao = os.getenv('AWS_REGION')
    return boto3.client('s3', region_name=regiao) if regiao else boto3.client('s3')


def gravar_json(s3, bucket, chave, documento):
    """Publica um documento no gold sem aceitar NaN/Infinity."""
    corpo = json.dumps(documento, ensure_ascii=False, indent=2, allow_nan=False)
    s3.put_object(
        Bucket=bucket,
        Key=chave,
        Body=corpo.encode('utf-8'),
        ContentType='application/json',
    )
    print(f'Gerado: {chave}')


def ler_csv_s3(s3, bucket, chave):
    texto = s3.get_object(Bucket=bucket, Key=chave)['Body'].read().decode('utf-8-sig')
    return list(csv.DictReader(io.StringIO(texto)))


# ---------------------------------------------------------------------------
# Leitura da Silver (uma única vez, compartilhada por todas as saídas)
# ---------------------------------------------------------------------------

def processar_linha(linha, cfg, arquivo):
    """Normaliza uma captura de _dados.csv; retorna None para linhas inválidas."""
    try:
        empresa = int(linha['id_empresa'])
        servidor = int(linha['id_servidor'])
        if empresa <= 0 or servidor <= 0:
            return None
        data = datetime.fromisoformat(linha['data_hora'].replace('Z', '+00:00'))
        if data.tzinfo is None:
            data = data.replace(tzinfo=cfg['fuso'])
        data = data.astimezone(timezone.utc)
        valores = {
            '_cpu': float(linha['cpu']),
            '_ram': float(linha['ram']),
            '_disco': float(linha['disco']),
        }
    except (KeyError, ValueError, TypeError, OverflowError):
        return None

    if not all(math.isfinite(valor) for valor in valores.values()):
        return None

    linha = dict(linha)
    linha.update(valores)
    linha['_empresa'] = empresa
    linha['_servidor'] = servidor
    linha['_data'] = data
    linha['_arquivo'] = arquivo
    return linha


def ler_amostras_silver(s3, cfg, agora):
    """Lê _dados.csv aplicando o filtro de 40 dias pela chave, antes do GET."""
    limite = agora.astimezone(cfg['fuso']).date() - timedelta(days=cfg['dias_historico'])
    servidores = defaultdict(dict)
    processados = ignorados = 0
    prefixo = cfg['silver'] + 'metricas/'

    paginas = s3.get_paginator('list_objects_v2').paginate(Bucket=cfg['bucket'], Prefix=prefixo)
    for pagina in paginas:
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
            for linha in ler_csv_s3(s3, cfg['bucket'], chave):
                amostra = processar_linha(linha, cfg, chave)
                if amostra:
                    servidores[(amostra['_empresa'], amostra['_servidor'])][amostra['_data']] = amostra

    print(
        f'Objetos silver: {processados} processados, '
        f'{ignorados} ignorados por terem mais de {cfg["dias_historico"]} dias.'
    )
    return servidores


def ler_processos_silver(s3, cfg):
    """Pega o *_processos.csv mais recente de cada servidor na silver."""
    prefixo = cfg['silver'] + 'processos/'
    ultimos = {}

    paginas = s3.get_paginator('list_objects_v2').paginate(Bucket=cfg['bucket'], Prefix=prefixo)
    for pagina in paginas:
        for objeto in pagina.get('Contents', []):
            chave = objeto['Key']
            correspondencia = PROCESSO_NA_CHAVE.search(chave)
            if not correspondencia:
                continue
            par = (int(correspondencia.group(1)), int(correspondencia.group(2)))
            dia = correspondencia.group(3)
            atual = ultimos.get(par)
            if atual is None or (dia, chave) > (atual['dia'], atual['chave']):
                ultimos[par] = {'dia': dia, 'chave': chave}

    processos = {}
    for par, info in ultimos.items():
        lista = []
        vistos = set()
        for linha in ler_csv_s3(s3, cfg['bucket'], info['chave']):
            try:
                pid = int(linha['PID'])
            except (KeyError, ValueError, TypeError):
                continue
            nome = (linha.get('Nome') or '').strip()
            usuario = (linha.get('Username') or '').strip()
            if not nome or (pid, nome) in vistos:
                continue
            vistos.add((pid, nome))
            lista.append({'pid': pid, 'nome': nome, 'user': usuario})
            if len(lista) >= 20:
                break
        processos[par] = lista

    return processos


# ---------------------------------------------------------------------------
# Cálculos compartilhados
# ---------------------------------------------------------------------------

def calcular_taxas_rede(lista, cfg):
    """Mbps de upload/download pela diferença dos contadores cumulativos."""
    taxas = {}
    anterior = None
    for amostra in lista:
        if anterior is not None:
            try:
                segundos = (amostra['_data'] - anterior['_data']).total_seconds()
                enviados = int(amostra['bytes_enviados']) - int(anterior['bytes_enviados'])
                recebidos = int(amostra['bytes_recebidos']) - int(anterior['bytes_recebidos'])
            except (KeyError, ValueError, TypeError, OverflowError):
                segundos = enviados = recebidos = 0

            if 0 < segundos <= cfg['rede_timeout'] and enviados >= 0 and recebidos >= 0:
                upload = enviados * 8 / segundos / 1_000_000
                download = recebidos * 8 / segundos / 1_000_000
                if math.isfinite(upload) and math.isfinite(download):
                    taxas[amostra['_data']] = (upload, download)
        anterior = amostra
    return taxas


def listas_por_empresa(servidores, empresa):
    """Listas ordenadas por servidor de uma empresa."""
    listas = {}
    for (id_empresa, id_servidor), amostras in servidores.items():
        if id_empresa == empresa:
            listas[id_servidor] = sorted(amostras.values(), key=lambda item: item['_data'])
    return listas


def ponto_servidor(lista, dia, agora, cfg):
    """Um dia de calendário no fuso local; hoje vai só até `agora`."""
    inicio = datetime.combine(dia, time.min, tzinfo=cfg['fuso']).astimezone(timezone.utc)
    if dia == agora.astimezone(cfg['fuso']).date():
        fim = agora
    else:
        fim = datetime.combine(dia + timedelta(days=1), time.min, tzinfo=cfg['fuso']).astimezone(timezone.utc)

    if not lista or fim <= lista[0]['_data']:
        return None

    inicio_efetivo = max(inicio, lista[0]['_data'])
    esperadas = (fim - inicio_efetivo).total_seconds() / cfg['frequencia']
    if esperadas <= 0:
        return None
    recebidas = sum(1 for amostra in lista if inicio_efetivo <= amostra['_data'] < fim)
    return min(100, recebidas / esperadas * 100), recebidas, esperadas


def serie_frota(listas, agora, cfg):
    """Disponibilidade ponderada por recebidas/esperadas nos últimos 30 dias."""
    hoje = agora.astimezone(cfg['fuso']).date()
    rotulos, valores = [], []
    for passo in range(cfg['dias_serie'] - 1, -1, -1):
        dia = hoje - timedelta(days=passo)
        soma_recebidas = soma_esperadas = 0
        for lista in listas:
            ponto = ponto_servidor(lista, dia, agora, cfg)
            if ponto is None:
                continue
            _, recebidas, esperadas = ponto
            soma_recebidas += recebidas
            soma_esperadas += esperadas
        valor = min(100, soma_recebidas / soma_esperadas * 100) if soma_esperadas else None
        rotulos.append(dia.strftime('%d/%m'))
        valores.append(round(valor, 1) if valor is not None else None)
    return {'rotulos': rotulos, 'valores': valores}


def captura_historica_valida(amostra):
    for campo in ('_cpu', '_ram', '_disco'):
        valor = amostra.get(campo)
        if not isinstance(valor, (int, float)) or not math.isfinite(valor) or not 0 <= valor <= 100:
            return False
    return True


def resumo_coleta(datas, frequencia):
    datas = sorted(set(datas))
    intervalos = [(segundo - primeiro).total_seconds() for primeiro, segundo in zip(datas, datas[1:])]
    if not datas:
        return {
            'capturas_validas': 0,
            'maior_intervalo_entre_capturas_segundos': 0,
            'intervalos_com_lacuna': 0,
            'capturas_esperadas_entre_extremos': 0,
        }
    return {
        'capturas_validas': len(datas),
        'maior_intervalo_entre_capturas_segundos': max(intervalos, default=0),
        'intervalos_com_lacuna': sum(delta > frequencia * 1.5 for delta in intervalos),
        'capturas_esperadas_entre_extremos': int((datas[-1] - datas[0]).total_seconds() // frequencia) + 1,
    }


def medias_de(capturas, campos):
    resultado = {}
    for campo in campos:
        validos = [item[campo] for item in capturas if item.get(campo) is not None]
        resultado[campo] = round(sum(validos) / len(validos), 1) if validos else None
    return resultado


# ---------------------------------------------------------------------------
# dash-geral.json — contrato v2 homologado (precedência sobre as demais saídas)
# ---------------------------------------------------------------------------

def construir_dash_geral(empresa, listas, agora, cfg):
    servidores_empresa = []
    for id_servidor, lista in listas.items():
        ultima = lista[-1]
        atraso = (agora - ultima['_data']).total_seconds()
        conexao = 'offline' if atraso > cfg['offline_timeout'] else 'online'
        servidores_empresa.append({
            'id_servidor': id_servidor,
            'conexao': conexao,
            'ultima_leitura': ultima['_data'].astimezone(cfg['fuso']).isoformat(),
            'atual': {
                'cpu': ultima['_cpu'],
                'ram': ultima['_ram'],
                'disco': ultima['_disco'],
            },
        })

    servidores_empresa.sort(key=lambda item: item['id_servidor'])
    frota = serie_frota(list(listas.values()), agora, cfg)
    return {
        'versao': 2,
        'id_empresa': empresa,
        'gerado_em': agora.astimezone(cfg['fuso']).isoformat(),
        'servidores': servidores_empresa,
        'disponibilidade_frota': {
            '7d': {
                'rotulos': frota['rotulos'][-7:],
                'valores': frota['valores'][-7:],
            },
            '30d': frota,
        },
    }


# ---------------------------------------------------------------------------
# dash-noc.json — saúde/capacidade em 24 h, com processos da silver
# ---------------------------------------------------------------------------

def construir_dash_noc(empresa, listas, agora, cfg, processos):
    inicio = agora - timedelta(hours=24)
    servidores_noc = []

    for id_servidor, lista in sorted(listas.items()):
        taxas = calcular_taxas_rede(lista, cfg)
        recursos = {}
        rede_por_minuto = {}

        for amostra in lista:
            minuto = amostra['_data'].astimezone(cfg['fuso']).replace(second=0, microsecond=0)
            if inicio <= amostra['_data'] <= agora:
                recursos[minuto] = amostra
                par = taxas.get(amostra['_data'])
                if par:
                    rede_por_minuto[minuto] = (par[1], par[0])  # (download, upload)

        ultima = lista[-1]
        atraso = (agora - ultima['_data']).total_seconds()
        upload, download = taxas.get(ultima['_data'], (None, None))

        series_24h = []
        for minuto, amostra in sorted(recursos.items()):
            down_mb, up_mb = rede_por_minuto.get(minuto, (None, None))
            series_24h.append({
                'horario': minuto.strftime('%Y-%m-%d %H:%M'),
                'cpu_pct': round(amostra['_cpu'], 2),
                'ram_pct': round(amostra['_ram'], 2),
                'disco_pct': round(amostra['_disco'], 2),
                'download_mbps': round(down_mb, 2) if down_mb is not None else None,
                'upload_mbps': round(up_mb, 2) if up_mb is not None else None,
            })

        servidores_noc.append({
            'id_servidor': id_servidor,
            'nome': ultima.get('username') or f'Servidor {id_servidor}',
            'ultima_coleta': ultima['_data'].astimezone(cfg['fuso']).isoformat(),
            'atraso_segundos': max(0, int(round(atraso))),
            'sem_telemetria': atraso > cfg['offline_timeout'],
            'metricas_atuais': {
                'cpu_pct': round(ultima['_cpu'], 2),
                'ram_pct': round(ultima['_ram'], 2),
                'disco_pct': round(ultima['_disco'], 2),
                'download_mbps': round(download, 2) if download is not None else None,
                'upload_mbps': round(upload, 2) if upload is not None else None,
            },
            'series_24h': series_24h,
            'processos': processos.get((empresa, id_servidor), []),
        })

    return {
        'id_empresa': empresa,
        'gerado_em': agora.astimezone(cfg['fuso']).isoformat(),
        'offline_timeout_segundos': cfg['offline_timeout'],
        'servidores': servidores_noc,
    }


# ---------------------------------------------------------------------------
# dash-capacidade.json — janela de planejamento
# ---------------------------------------------------------------------------

def construir_dash_capacidade(empresa, listas, agora, cfg):
    inicio_janela = agora - timedelta(days=cfg['janela_capacidade_dias'])
    servidores = []

    for id_servidor, lista in sorted(listas.items()):
        ultima = lista[-1]
        atraso = (agora - ultima['_data']).total_seconds()
        conexao = 'offline' if atraso > cfg['offline_timeout'] else 'online'
        amostras_janela = [
            amostra for amostra in lista
            if inicio_janela <= amostra['_data'] <= agora
        ]
        quantidade = len(amostras_janela)

        if quantidade:
            uso_medio = round(sum(a['_cpu'] for a in amostras_janela) / quantidade, 1)
            pico = round(max(a['_cpu'] for a in amostras_janela), 1)
            ram_media = round(sum(a['_ram'] for a in amostras_janela) / quantidade, 1)
            disco_medio = round(sum(a['_disco'] for a in amostras_janela) / quantidade, 1)
        else:
            uso_medio = pico = ram_media = disco_medio = None

        servidores.append({
            'id_servidor': id_servidor,
            'conexao': conexao,
            'ultima_leitura': ultima['_data'].isoformat(),
            'amostras_30d': quantidade,
            'uso_medio_30d': uso_medio,
            'pico_30d': pico,
            'ram_media_30d': ram_media,
            'disco_medio_30d': disco_medio,
        })

    return {
        'versao': 1,
        'id_empresa': empresa,
        'gerado_em': agora.isoformat(),
        'janela_dias': cfg['janela_capacidade_dias'],
        'servidores': servidores,
    }


# ---------------------------------------------------------------------------
# dash-operacional.json — KPIs, status e gráfico de recursos em 24 h
# ---------------------------------------------------------------------------

def construir_dash_operacional(empresa, listas, agora, cfg):
    inicio = agora - timedelta(hours=24)
    atuais = []
    upload_total = download_total = 0.0

    for id_servidor, lista in sorted(listas.items()):
        taxas = calcular_taxas_rede(lista, cfg)
        recursos = {}
        for amostra in lista:
            if inicio <= amostra['_data'] <= agora:
                minuto = amostra['_data'].astimezone(cfg['fuso']).replace(second=0, microsecond=0)
                recursos[minuto] = amostra

        ultima = lista[-1]
        atraso = (agora - ultima['_data']).total_seconds()
        if atraso > cfg['offline_timeout']:
            status = 'offline'
        elif (
            ultima['_cpu'] >= cfg['limites']['cpu']
            or ultima['_ram'] >= cfg['limites']['ram']
            or ultima['_disco'] >= cfg['limites']['disco']
        ):
            status = 'atencao'
        else:
            status = 'online'

        upload, download = taxas.get(ultima['_data'], (None, None))
        if status != 'offline' and upload is not None:
            upload_total += upload
            download_total += download

        grafico_recursos = []
        for minuto, amostra in sorted(recursos.items()):
            grafico_recursos.append({
                'horario': minuto.strftime('%Y-%m-%d %H:%M'),
                'cpu_pct': amostra['_cpu'],
                'ram_pct': amostra['_ram'],
                'disco_pct': amostra['_disco'],
            })

        if grafico_recursos:
            ponto = grafico_recursos[-1]
            metricas_atuais = {
                'cpu_pct': ponto['cpu_pct'],
                'ram_pct': ponto['ram_pct'],
                'disco_pct': ponto['disco_pct'],
            }
        else:
            metricas_atuais = {'cpu_pct': None, 'ram_pct': None, 'disco_pct': None}

        atuais.append({
            'id_servidor': id_servidor,
            'nome': ultima.get('username') or f'Servidor {id_servidor}',
            'status': status,
            'ultima_coleta': ultima['data_hora'],
            'metricas_atuais': metricas_atuais,
            'graficos': {'recursos_24h': grafico_recursos},
        })

    online = sum(1 for item in atuais if item['status'] == 'online')
    atencao = sum(1 for item in atuais if item['status'] == 'atencao')
    offline = sum(1 for item in atuais if item['status'] == 'offline')

    return {
        'id_empresa': empresa,
        'gerado_em': agora.astimezone(cfg['fuso']).isoformat(),
        'kpis': {
            'total_servidores': len(atuais),
            'servidores_online': online,
            'servidores_atencao': atencao,
            'servidores_offline': offline,
            'upload_mbps': round(upload_total, 2),
            'download_mbps': round(download_total, 2),
            'trafego_mbps': round(upload_total + download_total, 2),
        },
        'servidores': atuais,
    }


# ---------------------------------------------------------------------------
# dash-investigacao.json + partições históricas
# ---------------------------------------------------------------------------

def ler_manifesto(s3, bucket, chave):
    try:
        resposta = s3.get_object(Bucket=bucket, Key=chave)
        return json.loads(resposta['Body'].read().decode('utf-8'))
    except Exception as erro:  # noqa: BLE001 - S3 usa códigos diferentes para "não existe"
        codigo = getattr(erro, 'response', {}).get('Error', {}).get('Code')
        if codigo in ('NoSuchKey', '404', 'NotFound'):
            return {}
        raise


def publicar_particoes_investigacao(s3, bucket, raiz, empresa, id_servidor, amostras, taxas, cfg):
    """Grava uma partição por servidor/dia e devolve só os ponteiros."""
    dias = defaultdict(list)
    for amostra in amostras:
        if not captura_historica_valida(amostra):
            continue
        upload, download = taxas.get(amostra['_data'], (None, None))
        data = amostra['_data']
        dias[data.date().isoformat()].append({
            'timestamp': data.isoformat(timespec='microseconds').replace('+00:00', 'Z'),
            'id_empresa': empresa,
            'id_servidor': id_servidor,
            'nome_servidor': amostra.get('username') or f'Servidor {id_servidor}',
            'cpu_pct': amostra['_cpu'],
            'ram_pct': amostra['_ram'],
            'disco_pct': amostra['_disco'],
            'upload_mbps': upload,
            'download_mbps': download,
            'rede_mbps': upload + download if upload is not None and download is not None else None,
            'bytes_enviados': int(amostra['bytes_enviados']) if str(amostra.get('bytes_enviados', '')).isdigit() else None,
            'bytes_recebidos': int(amostra['bytes_recebidos']) if str(amostra.get('bytes_recebidos', '')).isdigit() else None,
        })

    particoes = []
    for dia, capturas in sorted(dias.items()):
        chave = f'{raiz}servidor_id={id_servidor}/data={dia}.json'
        manifesto = f'{raiz}metadados_particao/{hashlib.sha256(f"{id_servidor}:{dia}".encode()).hexdigest()}.json'
        identificacao = {'id_empresa': empresa, 'id_servidor': id_servidor, 'data_utc': dia}
        documento = {
            **identificacao,
            'frequencia_esperada_segundos': cfg['frequencia'],
            'capturas': capturas,
        }
        assinatura = hashlib.sha256(
            json.dumps(documento, ensure_ascii=False, allow_nan=False, sort_keys=True).encode('utf-8')
        ).hexdigest()

        if ler_manifesto(s3, bucket, manifesto).get('etag_origem') != assinatura:
            gravar_json(s3, bucket, chave, documento)
            gravar_json(s3, bucket, manifesto, {
                **identificacao,
                'etag_origem': assinatura,
                'chave_historico': chave,
                **resumo_coleta([
                    datetime.fromisoformat(item['timestamp'].replace('Z', '+00:00'))
                    for item in capturas
                ], cfg['frequencia']),
            })
        particoes.append(chave)

    capturas_validas = [amostra for amostra in amostras if captura_historica_valida(amostra)]
    if not capturas_validas:
        return None

    datas = [amostra['_data'] for amostra in capturas_validas]
    return {
        'id_servidor': id_servidor,
        'particoes': particoes,
        'primeira_captura': datas[0].isoformat(timespec='microseconds').replace('+00:00', 'Z'),
        'ultima_captura': datas[-1].isoformat(timespec='microseconds').replace('+00:00', 'Z'),
        'frequencia_esperada_segundos': cfg['frequencia'],
        **resumo_coleta(datas, cfg['frequencia']),
    }


def construir_dash_investigacao(s3, empresa, listas, agora, cfg):
    raiz = f"{cfg['gold']}empresa-{empresa}/dash-investigacao-historico/"
    limite = agora - timedelta(days=cfg['retencao_historico_dias'])
    historicos = []

    for id_servidor, lista in sorted(listas.items()):
        amostras_historicas = [
            amostra for amostra in lista
            if limite <= amostra['_data'] <= agora
        ]
        taxas = calcular_taxas_rede(lista, cfg)
        resumo = publicar_particoes_investigacao(
            s3, cfg['bucket'], raiz, empresa, id_servidor, amostras_historicas, taxas, cfg
        )
        if resumo:
            historicos.append(resumo)

    historicos.sort(key=lambda item: item['id_servidor'])
    metadados = {
        'id_empresa': empresa,
        'frequencia_esperada_segundos': cfg['frequencia'],
        'primeira_captura': min((item['primeira_captura'] for item in historicos), default=None),
        'ultima_captura': max((item['ultima_captura'] for item in historicos), default=None),
        'capturas_validas': sum(item['capturas_validas'] for item in historicos),
        'servidores': [
            {chave: valor for chave, valor in item.items() if chave != 'particoes'}
            for item in historicos
        ],
    }

    return {
        'versao': 1,
        'id_empresa': empresa,
        'gerado_em': agora.astimezone(cfg['fuso']).isoformat(),
        'colunas_captura': CAMPOS_CAPTURA,
        'historico': {
            'metadados': metadados,
            'layout': 'dash-investigacao-historico/servidor_id={id_servidor}/data={data}.json',
            'particoes': [chave for item in historicos for chave in item['particoes']],
        },
    }


# ---------------------------------------------------------------------------
# dash-metricas.json + partições diárias de métricas
# ---------------------------------------------------------------------------

def publicar_particoes_metricas(s3, bucket, raiz, empresa, id_servidor, lista, agora, cfg):
    """Replica as métricas agregadas da Sarah em arquivos por servidor/dia."""
    taxas = calcular_taxas_rede(lista, cfg)
    pontos = []
    origens = []

    for indice, amostra in enumerate(lista):
        upload, download = taxas.get(amostra['_data'], (None, None))
        rede = None
        if upload is not None and cfg['banda_mbps'] > 0:
            rede = round(min(100.0, (upload + download) / cfg['banda_mbps'] * 100), 1)

        ponto = {
            'dataHora': amostra['_data'].astimezone(cfg['fuso']).strftime('%Y-%m-%d %H:%M:%S'),
            'cpu': amostra['_cpu'],
            'ram': amostra['_ram'],
            'disco': amostra['_disco'],
            'rede': rede,
            'variacao': None,
        }
        if pontos:
            anterior = pontos[-1]
            ponto['variacao'] = {
                campo: (
                    round(ponto[campo] - anterior[campo], 1)
                    if ponto[campo] is not None and anterior[campo] is not None
                    else None
                )
                for campo in ('cpu', 'ram', 'disco', 'rede')
            }
        pontos.append(ponto)
        origens.append(amostra['_arquivo'])

    por_dia = defaultdict(lambda: defaultdict(list))
    for ponto, arquivo in zip(pontos, origens):
        por_dia[ponto['dataHora'][:10]][arquivo].append(ponto)

    nome = lista[-1].get('username') or f'Servidor {id_servidor}'
    particoes = []
    todas_as_medias = []

    for dia, blocos in sorted(por_dia.items()):
        horarios = []
        for arquivo, capturas in sorted(blocos.items(), key=lambda item: item[1][0]['dataHora']):
            medias = medias_de(capturas, ('cpu', 'ram', 'disco', 'rede'))
            todas_as_medias.append(medias)
            horarios.append({
                'lote': capturas[-1]['dataHora'],
                'arquivo': arquivo.rsplit('/', 1)[-1],
                'medias': medias,
                'capturas': capturas,
            })

        chave = f'{raiz}servidor_id={id_servidor}/data={dia}.json'
        gravar_json(s3, bucket, chave, {
            'id_empresa': empresa,
            'id_servidor': id_servidor,
            'nome_servidor': nome,
            'data': dia,
            'gerado_em': agora.isoformat(),
            'medias': medias_de([captura for bloco in blocos.values() for captura in bloco], ('cpu', 'ram', 'disco', 'rede')),
            'horarios': horarios,
        })
        particoes.append(chave)

    if not particoes:
        return None

    return {
        'id_servidor': id_servidor,
        'nome_servidor': nome,
        'primeira_captura': pontos[0]['dataHora'],
        'ultima_captura': pontos[-1]['dataHora'],
        'dias': len(particoes),
        'medias': medias_de(todas_as_medias, ('cpu', 'ram', 'disco', 'rede')),
        'particoes': particoes,
    }


def construir_dash_metricas(s3, empresa, listas, agora, cfg):
    raiz = f"{cfg['gold']}empresa-{empresa}/dash-metricas/"
    servidores = []

    for id_servidor, lista in sorted(listas.items()):
        resumo = publicar_particoes_metricas(
            s3, cfg['bucket'], raiz, empresa, id_servidor, lista, agora, cfg
        )
        if resumo:
            servidores.append(resumo)

    return {
        'versao': 1,
        'id_empresa': empresa,
        'gerado_em': agora.isoformat(),
        'layout': 'dash-metricas/servidor_id={id_servidor}/data={data}.json',
        'servidores': servidores,
    }


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------

def run(s3=None, agora=None):
    cfg = carregar_config()
    s3 = s3 or obter_cliente_s3()
    agora = agora or datetime.now(timezone.utc)
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=timezone.utc)
    agora = agora.astimezone(timezone.utc)

    servidores = ler_amostras_silver(s3, cfg, agora)
    processos = ler_processos_silver(s3, cfg)
    empresas = sorted({empresa for empresa, _ in servidores})

    for empresa in empresas:
        listas = listas_por_empresa(servidores, empresa)
        documentos = {
            'dash-geral.json': construir_dash_geral(empresa, listas, agora, cfg),
            'dash-noc.json': construir_dash_noc(empresa, listas, agora, cfg, processos),
            'dash-capacidade.json': construir_dash_capacidade(empresa, listas, agora, cfg),
            'dash-operacional.json': construir_dash_operacional(empresa, listas, agora, cfg),
            'dash-investigacao.json': construir_dash_investigacao(s3, empresa, listas, agora, cfg),
            'dash-metricas.json': construir_dash_metricas(s3, empresa, listas, agora, cfg),
        }
        for nome, documento in documentos.items():
            gravar_json(
                s3,
                cfg['bucket'],
                f"{cfg['gold']}empresa-{empresa}/{nome}",
                documento,
            )

    print('Silver -> Gold concluído.')


def lambda_handler(evento, contexto):
    run()
    return {'etapa': 'silver2gold', 'status': 'concluido'}


if __name__ == '__main__':
    run()
