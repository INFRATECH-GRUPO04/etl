import csv
import hashlib
import io
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import boto3


def carregar_configuracao():
    """Carrega o .env local da ETL e devolve as configurações de execução."""
    caminho_env = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(caminho_env):
        with open(caminho_env, encoding='utf-8') as arquivo:
            for linha in arquivo:
                if '=' in linha and not linha.lstrip().startswith('#'):
                    chave, valor = linha.strip().split('=', 1)
                    os.environ.setdefault(chave, valor.strip('"\''))

    bucket = os.getenv('S3_BUCKET')
    if not bucket:
        raise RuntimeError('Defina S3_BUCKET no ambiente ou no arquivo .env.')

    frequencia = int(os.getenv('CAPTURA_INTERVALO_SEGUNDOS', '60'))
    rede_timeout = int(os.getenv('REDE_MAX_INTERVAL_SECONDS', '90'))
    if frequencia <= 0:
        raise ValueError('CAPTURA_INTERVALO_SEGUNDOS deve ser positivo.')
    if rede_timeout <= 0:
        raise ValueError('REDE_MAX_INTERVAL_SECONDS deve ser positivo.')

    return {
        'bucket': bucket,
        'silver': os.getenv('S3_SILVER_PREFIX', '02-silver/').strip('/') + '/',
        'gold': os.getenv('S3_GOLD_PREFIX', '03-gold/').strip('/') + '/',
        'offline_timeout': int(os.getenv('OFFLINE_TIMEOUT_SECONDS', '900')),
        'rede_timeout': rede_timeout,
        'frequencia': frequencia,
        'limites': {
            campo: float(os.getenv(f'LIMITE_{campo.upper()}_PCT', '80'))
            for campo in ('cpu', 'ram', 'disco')
        },
        'fuso': ZoneInfo(os.getenv('TIMEZONE', 'America/Sao_Paulo')),
    }


def contador(amostra, campo):
    """Devolve um contador inteiro não negativo ou None quando ele não é válido."""
    try:
        valor = int(amostra[campo])
        return valor if valor >= 0 else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def normalizar_data(valor, fuso):
    """Converte timestamps com fuso para UTC e interpreta horários sem fuso como locais."""
    texto = str(valor or '').strip()
    if not texto:
        raise ValueError('A captura não contém data e horário.')
    data = datetime.fromisoformat(texto.replace('Z', '+00:00'))
    if data.tzinfo is None:
        data = data.replace(tzinfo=fuso)
    return data.astimezone(timezone.utc)


def ler_silver(s3, bucket, prefixo, fuso):
    """Lê CSVs tratados da Silver e mantém uma amostra válida por servidor e instante."""
    servidores = defaultdict(dict)
    paginas = s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefixo + 'metricas/')

    for pagina in paginas:
        for objeto in pagina.get('Contents', []):
            chave = objeto['Key']
            if not chave.endswith('_dados.csv'):
                continue

            resposta = s3.get_object(Bucket=bucket, Key=chave, IfMatch=objeto.get('ETag'))
            texto = resposta['Body'].read().decode('utf-8-sig')
            for linha in csv.DictReader(io.StringIO(texto)):
                try:
                    empresa = int(linha['id_empresa'])
                    id_servidor = int(linha['id_servidor'])
                    if empresa <= 0 or id_servidor <= 0:
                        continue

                    data = normalizar_data(linha.get('data_hora'), fuso)
                    valores = {}
                    for campo in ('cpu', 'ram', 'disco'):
                        valor = float(linha[campo])
                        if not math.isfinite(valor) or not 0 <= valor <= 100:
                            raise ValueError(f'{campo} fora do intervalo permitido.')
                        valores[f'_{campo}'] = valor

                    linha['_data'] = data
                    linha.update(valores)
                    servidores[(empresa, id_servidor)][data] = linha
                except (KeyError, ValueError, TypeError, OverflowError):
                    continue

    return servidores


def calcular_taxas(lista, rede_timeout):
    """Calcula Mbps entre capturas consecutivas e ignora intervalos inválidos ou reinícios."""
    taxas = {}
    anterior = None
    for atual in lista:
        if anterior is not None:
            segundos = (atual['_data'] - anterior['_data']).total_seconds()
            enviados_atual = contador(atual, 'bytes_enviados')
            enviados_anterior = contador(anterior, 'bytes_enviados')
            recebidos_atual = contador(atual, 'bytes_recebidos')
            recebidos_anterior = contador(anterior, 'bytes_recebidos')

            if (0 < segundos <= rede_timeout
                    and None not in (enviados_atual, enviados_anterior, recebidos_atual, recebidos_anterior)):
                enviados = enviados_atual - enviados_anterior
                recebidos = recebidos_atual - recebidos_anterior
                if enviados >= 0 and recebidos >= 0:
                    taxas[atual['_data']] = (
                        enviados * 8 / segundos / 1_000_000,
                        recebidos * 8 / segundos / 1_000_000,
                    )
        anterior = atual
    return taxas


def resumo_coleta(datas, frequencia):
    # (Claudia - M): Resume a cobertura observada sem preencher lacunas com valores fictícios.
    datas = sorted(set(datas))
    intervalos = [(b - a).total_seconds() for a, b in zip(datas, datas[1:])]
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


def gravar_json(s3, bucket, chave, documento):
    """Publica JSON UTF-8 e impede que valores NaN ou infinitos cheguem à Gold."""
    conteudo = json.dumps(documento, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    s3.put_object(Bucket=bucket, Key=chave, Body=conteudo, ContentType='application/json')


def ler_manifesto(s3, bucket, chave):
    """Lê o manifesto de uma partição; objeto ausente significa que ela ainda não foi publicada."""
    try:
        resposta = s3.get_object(Bucket=bucket, Key=chave)
        return json.loads(resposta['Body'].read().decode('utf-8'))
    except Exception as erro:
        codigo = getattr(erro, 'response', {}).get('Error', {}).get('Code')
        if codigo in ('NoSuchKey', '404', 'NotFound'):
            return {}
        raise


def publicar_historico(s3, bucket, raiz, empresa, id_servidor, amostras, taxas, frequencia):
    """Publica capturas em partições diárias UTC e retorna os metadados do servidor."""
    dias = defaultdict(list)
    for amostra in amostras:
        data = amostra['_data']
        upload, download = taxas.get(data, (None, None))
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
            'bytes_enviados': contador(amostra, 'bytes_enviados'),
            'bytes_recebidos': contador(amostra, 'bytes_recebidos'),
        })

    particoes = []
    for dia, capturas in sorted(dias.items()):
        chave = f'{raiz}historico/servidor_id={id_servidor}/data={dia}.json'
        manifesto = f'{raiz}historico/metadados_particao/{hashlib.sha256(f"{id_servidor}:{dia}".encode()).hexdigest()}.json'
        identificacao = {'id_empresa': empresa, 'id_servidor': id_servidor, 'data_utc': dia}
        documento = {
            **identificacao,
            'frequencia_esperada_segundos': frequencia,
            'capturas': capturas,
        }
        etag_origem = hashlib.sha256(json.dumps(documento, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

        if ler_manifesto(s3, bucket, manifesto).get('etag_origem') != etag_origem:
            gravar_json(s3, bucket, chave, documento)
            gravar_json(s3, bucket, manifesto, {
                **identificacao,
                'etag_origem': etag_origem,
                'chave_historico': chave,
                **resumo_coleta([datetime.fromisoformat(item['timestamp'].replace('Z', '+00:00'))
                                 for item in capturas], frequencia),
            })
        particoes.append(chave)

    return {
        'id_servidor': id_servidor,
        'particoes': particoes,
        'primeira_captura': amostras[0]['_data'].isoformat(timespec='microseconds').replace('+00:00', 'Z'),
        'ultima_captura': amostras[-1]['_data'].isoformat(timespec='microseconds').replace('+00:00', 'Z'),
        'frequencia_esperada_segundos': frequencia,
        **resumo_coleta([item['_data'] for item in amostras], frequencia),
    }


def montar_dashboard_servidor(id_servidor, lista, taxas, agora, fuso, limites, offline_timeout):
    """Monta o estado atual de um servidor sem confundir status atual com o histórico Gold."""
    ultima = lista[-1]
    inicio = agora - timedelta(hours=24)
    recursos_por_minuto = {}
    for amostra in lista:
        data = amostra['_data']
        if inicio <= data <= agora:
            minuto = data.astimezone(fuso).replace(second=0, microsecond=0)
            recursos_por_minuto[minuto] = amostra

    atraso = (agora - ultima['_data']).total_seconds()
    if atraso > offline_timeout:
        status = 'offline'
    elif any(ultima[f'_{campo}'] >= limite for campo, limite in limites.items()):
        status = 'atencao'
    else:
        status = 'online'

    graficos = [{
        'horario': minuto.strftime('%Y-%m-%d %H:%M'),
        'cpu_pct': amostra['_cpu'],
        'ram_pct': amostra['_ram'],
        'disco_pct': amostra['_disco'],
    } for minuto, amostra in sorted(recursos_por_minuto.items())]
    ponto_atual = graficos[-1] if graficos else {'cpu_pct': None, 'ram_pct': None, 'disco_pct': None}
    instante = ultima['_data'].isoformat(timespec='microseconds').replace('+00:00', 'Z')

    return {
        'id_servidor': id_servidor,
        'nome': ultima.get('username') or f'Servidor {id_servidor}',
        'status': status,
        'ultima_coleta': instante,
        'metricas_atuais': {
            'cpu_pct': ponto_atual['cpu_pct'],
            'ram_pct': ponto_atual['ram_pct'],
            'disco_pct': ponto_atual['disco_pct'],
        },
        'graficos': {'recursos_24h': graficos},
    }


def publicar_empresa(s3, bucket, gold, empresa, servidores, agora, fuso, limites,
                    offline_timeout, rede_timeout, frequencia):
    """Publica o resumo legado da Dashboard Geral e o histórico da investigação."""
    raiz = f'{gold}empresa-{empresa}/'
    atuais = []
    historicos = []
    upload_atual = download_atual = 0.0

    for (id_empresa, id_servidor), registros in sorted(servidores.items()):
        if id_empresa != empresa:
            continue
        lista = sorted(registros.values(), key=lambda item: item['_data'])
        taxas = calcular_taxas(lista, rede_timeout)
        historicos.append(publicar_historico(
            s3, bucket, raiz, empresa, id_servidor, lista, taxas, frequencia
        ))
        servidor_atual = montar_dashboard_servidor(
            id_servidor, lista, taxas, agora, fuso, limites, offline_timeout
        )
        atuais.append(servidor_atual)

        if servidor_atual['status'] != 'offline':
            taxas_ultima = taxas.get(lista[-1]['_data'])
            if taxas_ultima is not None:
                upload_atual += taxas_ultima[0]
                download_atual += taxas_ultima[1]

    atuais.sort(key=lambda item: item['id_servidor'])
    historicos.sort(key=lambda item: item['id_servidor'])
    if not historicos:
        return

    online = sum(item['status'] == 'online' for item in atuais)
    atencao = sum(item['status'] == 'atencao' for item in atuais)
    offline = sum(item['status'] == 'offline' for item in atuais)
    dashboard = {
        'id_empresa': empresa,
        'gerado_em': agora.isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'kpis': {
            'total_servidores': len(atuais),
            'servidores_online': online,
            'servidores_atencao': atencao,
            'servidores_offline': offline,
            'upload_mbps': round(upload_atual, 2),
            'download_mbps': round(download_atual, 2),
            'trafego_mbps': round(upload_atual + download_atual, 2),
        },
        'servidores': atuais,
    }

    # (Claudia - M): Os metadados delimitam o histórico real de cada servidor e da empresa.
    metadados = {
        'id_empresa': empresa,
        'frequencia_esperada_segundos': frequencia,
        'primeira_captura': min(item['primeira_captura'] for item in historicos),
        'ultima_captura': max(item['ultima_captura'] for item in historicos),
        'capturas_validas': sum(item['capturas_validas'] for item in historicos),
        'servidores': historicos,
    }
    gravar_json(s3, bucket, f'{raiz}historico/metadados.json', metadados)
    gravar_json(s3, bucket, f'{raiz}dashboard.json', dashboard)
    print(f'Gold publicada para empresa {empresa}: {len(atuais)} servidores.')


def run(s3=None, *, agora=None):
    """Lê a Silver e publica, por empresa, os documentos compatíveis da Gold."""
    configuracao = carregar_configuracao()
    if s3 is None:
        regiao = os.getenv('AWS_REGION')
        s3 = boto3.client('s3', region_name=regiao) if regiao else boto3.client('s3')

    instante_atual = agora or datetime.now(timezone.utc)
    if instante_atual.tzinfo is None:
        instante_atual = instante_atual.replace(tzinfo=timezone.utc)
    instante_atual = instante_atual.astimezone(timezone.utc)
    servidores = ler_silver(s3, configuracao['bucket'], configuracao['silver'], configuracao['fuso'])

    empresas = sorted({empresa for empresa, _ in servidores})
    for empresa in empresas:
        publicar_empresa(
            s3=s3,
            bucket=configuracao['bucket'],
            gold=configuracao['gold'],
            empresa=empresa,
            servidores=servidores,
            agora=instante_atual,
            fuso=configuracao['fuso'],
            limites=configuracao['limites'],
            offline_timeout=configuracao['offline_timeout'],
            rede_timeout=configuracao['rede_timeout'],
            frequencia=configuracao['frequencia'],
        )
    print('Silver -> Gold concluído.')


def lambda_handler(evento, contexto):
    """Ponto de entrada para execução periódica na AWS Lambda."""
    run()
    return {'etapa': 'silver2gold', 'status': 'concluido'}


if __name__ == '__main__':
    run()
