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


# (Cláudia - M) Define o período móvel mantido no histórico consolidado da Gold.
GOLD_RETENTION_DAYS = 180


def carregar_configuracao():
    """Carrega as configurações locais e os parâmetros operacionais da ETL."""
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

    # (Cláudia - M) Usa uma janela compatível com capturas de aproximadamente 60 segundos
    # para que o histórico da dashboard possa calcular taxas de rede entre coletas válidas.
    rede_timeout = int(os.getenv('REDE_MAX_INTERVAL_SECONDS', '90'))
    frequencia = int(os.getenv('CAPTURA_INTERVALO_SEGUNDOS', '60'))
    if rede_timeout <= 0 or frequencia <= 0:
        raise ValueError('Os intervalos de rede e captura devem ser positivos.')

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


# Lê os CSVs da Silver preservando a organização original por empresa, servidor e timestamp.
def ler_silver(s3, bucket, prefixo, fuso):
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
                    servidor = int(linha['id_servidor'])
                    if empresa <= 0 or servidor <= 0:
                        continue
                    data = datetime.fromisoformat(linha['data_hora'].replace('Z', '+00:00'))
                    if data.tzinfo is None:
                        data = data.replace(tzinfo=fuso)
                    data = data.astimezone(timezone.utc)
                    linha['_data'] = data
                    linha['_cpu'] = float(linha['cpu'])
                    linha['_ram'] = float(linha['ram'])
                    linha['_disco'] = float(linha['disco'])
                    servidores[(empresa, servidor)][data] = linha
                except (KeyError, ValueError, TypeError, OverflowError):
                    continue

    return servidores


# (Cláudia - M) Valida os campos de recurso antes de incluí-los na Gold histórica;
# capturas inválidas continuam fora da análise sem mudar a regra operacional original.
def captura_historica_valida(amostra):
    for campo in ('_cpu', '_ram', '_disco'):
        valor = amostra.get(campo)
        if not isinstance(valor, (int, float)) or not math.isfinite(valor) or not 0 <= valor <= 100:
            return False
    return True


# (Cláudia - M) Resume a quantidade de capturas e as lacunas observadas,
# para que a dashboard calcule cobertura sem preencher ausências com zeros.
def resumo_coleta(datas, frequencia):
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


# (Cláudia - M) Escreve documentos históricos JSON sem aceitar valores NaN ou infinitos,
# que impediriam o backend de interpretar as métricas da Gold corretamente.
def gravar_json_historico(s3, bucket, chave, documento):
    conteudo = json.dumps(documento, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    s3.put_object(Bucket=bucket, Key=chave, Body=conteudo, ContentType='application/json')


# (Cláudia - M) Consulta o manifesto da partição para evitar republicar JSON histórico
# quando o conteúdo de origem não mudou; erros diferentes de objeto ausente continuam visíveis.
def ler_manifesto(s3, bucket, chave):
    try:
        resposta = s3.get_object(Bucket=bucket, Key=chave)
        return json.loads(resposta['Body'].read().decode('utf-8'))
    except Exception as erro:
        codigo = getattr(erro, 'response', {}).get('Error', {}).get('Code')
        if codigo in ('NoSuchKey', '404', 'NotFound'):
            return {}
        raise


# (Cláudia - M) Organiza as capturas válidas por servidor e dia UTC e publica as partições
# que os endpoints da investigação usam para consultar o histórico real no S3.
def publicar_historico(s3, bucket, raiz, empresa, id_servidor, amostras, taxas, frequencia,
                       consolidar=False):
    dias = defaultdict(list)
    particoes_consolidadas = []
    for amostra in amostras:
        if not captura_historica_valida(amostra):
            continue
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
            'bytes_enviados': int(amostra['bytes_enviados']) if str(amostra.get('bytes_enviados', '')).isdigit() else None,
            'bytes_recebidos': int(amostra['bytes_recebidos']) if str(amostra.get('bytes_recebidos', '')).isdigit() else None,
        })

    particoes = []
    for dia, capturas in sorted(dias.items()):
        chave = f'{raiz}historico/servidor_id={id_servidor}/data={dia}.json'
        manifesto = f'{raiz}historico/metadados_particao/{hashlib.sha256(f"{id_servidor}:{dia}".encode()).hexdigest()}.json'
        identificacao = {'id_empresa': empresa, 'id_servidor': id_servidor, 'data_utc': dia}
        documento = {**identificacao, 'frequencia_esperada_segundos': frequencia, 'capturas': capturas}
        assinatura_origem = hashlib.sha256(
            json.dumps(documento, ensure_ascii=False, allow_nan=False).encode('utf-8')
        ).hexdigest()

        if consolidar or ler_manifesto(s3, bucket, manifesto).get('etag_origem') != assinatura_origem:
            manifesto_documento = {
                **identificacao,
                'etag_origem': assinatura_origem,
                'chave_historico': chave,
                **resumo_coleta([
                    datetime.fromisoformat(item['timestamp'].replace('Z', '+00:00'))
                    for item in capturas
                ], frequencia),
            }
            if consolidar:
                particoes_consolidadas.append({
                    'documento': documento,
                    'manifesto': manifesto_documento,
                })
            else:
                gravar_json_historico(s3, bucket, chave, documento)
                gravar_json_historico(s3, bucket, manifesto, manifesto_documento)
        particoes.append(chave)

    capturas_validas = [amostra for amostra in amostras if captura_historica_valida(amostra)]
    if not capturas_validas:
        return None
    datas_validas = [item['_data'] for item in capturas_validas]
    resultado = {
        'id_servidor': id_servidor,
        'particoes': particoes,
        'primeira_captura': datas_validas[0].isoformat(timespec='microseconds').replace('+00:00', 'Z'),
        'ultima_captura': datas_validas[-1].isoformat(timespec='microseconds').replace('+00:00', 'Z'),
        'frequencia_esperada_segundos': frequencia,
        **resumo_coleta(datas_validas, frequencia),
    }
    if consolidar:
        resultado['particoes_consolidadas'] = particoes_consolidadas
    return resultado


def publicar_empresa(s3, bucket, gold, empresa, servidores, agora, fuso, limites,
                     offline_timeout, rede_timeout, frequencia):
    """Preserva o resumo operacional original e acrescenta os objetos históricos da Cláudia."""
    raiz = f'{gold}empresa-{empresa}/'
    atuais = []
    # (Cláudia - M) Reúne somente servidores com capturas válidas para montar
    # os limites de datas e as partições disponíveis na Gold histórica.
    historicos = []
    particoes_historicas = []
    upload_atual = download_atual = 0
    limite_inferior_retencao = agora - timedelta(days=GOLD_RETENTION_DAYS)

    for (id_empresa, id_servidor), amostras in servidores.items():
        if id_empresa != empresa:
            continue
        lista = sorted(amostras.values(), key=lambda x: x['_data'])
        ultima = lista[-1]
        taxas = {}
        recursos = {}
        anterior = None

        for amostra in lista:
            data = amostra['_data']
            minuto = data.astimezone(fuso).replace(second=0, microsecond=0)

            # Guarda a última captura de cada minuto para o gráfico deste servidor.
            if agora - timedelta(hours=24) <= data <= agora:
                recursos[minuto] = amostra

            # (Cláudia - M) Usa a mesma taxa calculada para a saída operacional
            # ao montar as métricas de rede das capturas históricas, evitando fórmula divergente.
            if anterior:
                segundos = (data - anterior['_data']).total_seconds()
                try:
                    enviados = int(amostra['bytes_enviados']) - int(anterior['bytes_enviados'])
                    recebidos = int(amostra['bytes_recebidos']) - int(anterior['bytes_recebidos'])
                    if 0 < segundos <= rede_timeout and enviados >= 0 and recebidos >= 0:
                        up = enviados * 8 / segundos / 1_000_000
                        down = recebidos * 8 / segundos / 1_000_000
                        taxas[data] = (up, down)
                except (KeyError, ValueError, TypeError, OverflowError):
                    pass
            anterior = amostra

        # (Cláudia - M) Publica o histórico deste servidor sem alterar os registros
        # usados para status, KPIs e gráficos operacionais das outras dashboards.
        # (Cláudia - M) Filtra a janela inclusiva em UTC apenas para o histórico consolidado.
        amostras_historicas = [
            amostra for amostra in lista
            if limite_inferior_retencao <= amostra['_data'] <= agora
        ]
        historico_servidor = publicar_historico(
            s3, bucket, raiz, empresa, id_servidor, amostras_historicas, taxas, frequencia,
            consolidar=True
        )
        if historico_servidor:
            particoes_historicas.extend(historico_servidor.pop('particoes_consolidadas'))
            historicos.append(historico_servidor)

        atraso = (agora - ultima['_data']).total_seconds()
        if atraso > offline_timeout:
            status = 'offline'
        elif ultima['_cpu'] >= limites['cpu'] or ultima['_ram'] >= limites['ram'] or ultima['_disco'] >= limites['disco']:
            status = 'atencao'
        else:
            status = 'online'

        up, down = taxas.get(ultima['_data'], (None, None))
        if status != 'offline' and up is not None:
            upload_atual += up
            download_atual += down

        grafico_recursos = []
        for minuto, amostra in sorted(recursos.items()):
            grafico_recursos.append({
                'horario': minuto.strftime('%Y-%m-%d %H:%M'),
                'cpu_pct': amostra['_cpu'],
                'ram_pct': amostra['_ram'],
                'disco_pct': amostra['_disco'],
            })

        if grafico_recursos:
            ultimo_ponto = grafico_recursos[-1]
            metricas_atuais = {
                'cpu_pct': ultimo_ponto['cpu_pct'],
                'ram_pct': ultimo_ponto['ram_pct'],
                'disco_pct': ultimo_ponto['disco_pct'],
            }
        else:
            metricas_atuais = {'cpu_pct': None, 'ram_pct': None, 'disco_pct': None}

        # Mantém o formato da data_hora de origem exigido pelo dashboard.json original.
        atuais.append({
            'id_servidor': id_servidor,
            'nome': ultima.get('username') or f'Servidor {id_servidor}',
            'status': status,
            'ultima_coleta': ultima['data_hora'],
            'metricas_atuais': metricas_atuais,
            'graficos': {'recursos_24h': grafico_recursos},
        })

    online = sum(1 for servidor in atuais if servidor['status'] == 'online')
    atencao = sum(1 for servidor in atuais if servidor['status'] == 'atencao')
    offline = sum(1 for servidor in atuais if servidor['status'] == 'offline')
    documento = {
        'id_empresa': empresa,
        'gerado_em': agora.isoformat(),
        'kpis': {
            'total_servidores': len(atuais),
            'servidores_online': online,
            'servidores_atencao': atencao,
            'servidores_offline': offline,
            'upload_mbps': round(upload_atual, 2),
            'download_mbps': round(download_atual, 2),
            'trafego_mbps': round(upload_atual + download_atual, 2),
        },
        'servidores': sorted(atuais, key=lambda servidor: servidor['id_servidor']),
    }

    # (Cláudia - M) Publica os limites de data e as partições por servidor
    # que alimentam os filtros e a leitura histórica dos endpoints de investigação.
    historicos.sort(key=lambda item: item['id_servidor'])
    metadados = {
        'id_empresa': empresa,
        'frequencia_esperada_segundos': frequencia,
        'primeira_captura': min((item['primeira_captura'] for item in historicos), default=None),
        'ultima_captura': max((item['ultima_captura'] for item in historicos), default=None),
        'capturas_validas': sum(item['capturas_validas'] for item in historicos),
        'servidores': historicos,
    }
    # (Cláudia - M) Reúne metadados, partições e manifestos no mesmo dashboard.json,
    # mantendo os dados operacionais legados no nível superior do documento.
    # (Cláudia - M) Declara uma vez os campos das capturas e valida os IDs antes de
    # removê-los das linhas, mantendo empresa e servidor identificados na partição.
    colunas_captura = [
        'timestamp', 'nome_servidor', 'cpu_pct', 'ram_pct', 'disco_pct',
        'upload_mbps', 'download_mbps', 'rede_mbps', 'bytes_enviados',
        'bytes_recebidos',
    ]
    particoes_compactadas = []
    for particao in particoes_historicas:
        documento_particao = particao['documento']
        id_empresa_particao = documento_particao['id_empresa']
        id_servidor_particao = documento_particao['id_servidor']
        capturas = documento_particao['capturas']
        if any(
            captura['id_empresa'] != id_empresa_particao
            or captura['id_servidor'] != id_servidor_particao
            for captura in capturas
        ):
            raise ValueError('Os IDs das capturas não correspondem à partição histórica.')
        particoes_compactadas.append({
            'documento': {
                **{campo: valor for campo, valor in documento_particao.items() if campo != 'capturas'},
                'capturas': [[captura[campo] for campo in colunas_captura] for captura in capturas],
            },
            'manifesto': particao['manifesto'],
        })

    documento['historico'] = {
        'metadados': metadados,
        'colunas_captura': colunas_captura,
        'particoes_historicas': particoes_compactadas,
    }

    # Preserva a gravação legada para manter inalterado o JSON das dashboards existentes.
    chave = f'{gold}empresa-{empresa}/dashboard.json'
    s3.put_object(
        Bucket=bucket,
        Key=chave,
        Body=json.dumps(documento, ensure_ascii=False, separators=(',', ':')),
        ContentType='application/json'
    )
    print(f'Gerado: {chave} ({len(atuais)} servidores)')


def run(s3=None, *, agora=None):
    """Lê a Silver e publica, por empresa, a Gold operacional e a histórica."""
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
    """Ponto de entrada preservado para execução pela AWS Lambda."""
    run()
    return {'etapa': 'silver2gold', 'status': 'concluido'}


if __name__ == '__main__':
    run()
