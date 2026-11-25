import csv  # para lidar com arquivos .csv
import io  # leitura e escrita de arquivos
import json  # para lidar com arquivos .json
import math  # para arredondar a escala do grafico de escalabilidade
import os
from datetime import datetime, timedelta, timezone  # formatar horario
from zoneinfo import ZoneInfo  # fusohorario
from collections import defaultdict  # permite acessar listas vazias sem dar erro
import boto3  # SDK da AWS (usa IAM Role na EC2 ou credenciais do .env)


def dash_felipe():
    # Lê o .env, se existir. Na EC2, o boto3 usa a IAM Role.
    if os.path.exists('.env'):
        for linha in open('.env', encoding='utf-8'):
            if '=' in linha and not linha.lstrip().startswith('#'):
                chave, valor = linha.strip().split('=', 1)
                os.environ.setdefault(chave, valor.strip('"\''))

    # Atribui sozinho se nao tiver colocado no .env
    BUCKET = os.getenv('S3_BUCKET')
    SILVER = os.getenv('S3_SILVER_PREFIX', '02-silver/').strip('/') + '/'
    GOLD = os.getenv('S3_GOLD_PREFIX', '03-gold/').strip('/') + '/'
    OFFLINE_TIMEOUT = int(os.getenv('OFFLINE_TIMEOUT_SECONDS', '900'))
    REDE_TIMEOUT = int(os.getenv('REDE_MAX_INTERVAL_SECONDS', '120'))  # 120s tolera coletas de 60s
    LIMITE_CRITICO_CPU = float(os.getenv('LIMITE_CRITICO_CPU_PCT', '90'))
    CAPACIDADE_REDE_MBPS = float(os.getenv('CAPACIDADE_REDE_MBPS', '100'))
    FUSO_LOCAL = ZoneInfo(os.getenv('TIMEZONE', 'America/Sao_Paulo'))

    # ---------------- FUNÇÕES AUXILIARES INTERNAS ----------------

    def calcular_p95(valores):
        """Ordena os valores das ultimas 24h e pega o valor na posicao de 95% (ignora picos isolados)."""
        if not valores:
            return 0.0
        ordenados = sorted(valores)
        indice = min(int(len(ordenados) * 0.95), len(ordenados) - 1)
        return round(ordenados[indice], 1)

    def calcular_eficiencia_termica(temp_c, temp_ideal=35.0, temp_critica=95.0):
        """Converte temperatura (C) para eficiencia termica (%): quanto mais frio, mais perto de 100%."""
        eficiencia = 100.0 - ((temp_c - temp_ideal) / (temp_critica - temp_ideal) * 100.0)
        return round(max(0.0, min(100.0, eficiencia)), 1)

    def gerar_projecao_escalabilidade(jogadores_atuais, cpu_atual, ram_atual_gb, rede_atual_mbps):
        """Calcula o custo de 1 jogador e projeta 11 pontos dinamicos ate o limite critico da maquina."""
        # 1. Quanto cada jogador consome sozinho (consumo atual / jogadores atuais)
        custo_cpu = max(0.01, cpu_atual / max(1, jogadores_atuais))
        custo_ram = max(0.001, ram_atual_gb / max(1, jogadores_atuais))
        custo_rede = max(0.001, rede_atual_mbps / max(1, jogadores_atuais))

        # 2. Maximo de jogadores antes de bater o limite critico de CPU (90%)
        max_jogadores = int(LIMITE_CRITICO_CPU / custo_cpu)

        # 3. Define um teto redondo para o eixo X (funciona para 100 ou 1000+ jogadores)
        alvo = max(max_jogadores * 1.15, 20)
        magnitude = 10 ** (len(str(int(alvo))) - 1)
        passo_base = magnitude / 2 if alvo / magnitude <= 5 else magnitude
        teto_grafico = int(math.ceil(alvo / passo_base) * passo_base)

        # Divide o eixo X sempre em 10 intervalos (11 pontos no total)
        passo = max(1, teto_grafico // 10)
        projecao = []
        for qtd in range(0, teto_grafico + 1, passo):
            projecao.append({
                'jogadores': qtd,
                'cpu_pct': round(min(100.0, custo_cpu * qtd), 1),
                'ram_gb': round(custo_ram * qtd, 2),
                'rede_mbps': round(custo_rede * qtd, 2),
            })

        return {
            'max_jogadores_suportados': max_jogadores,
            'consumo_unitario_por_jogador': {
                'cpu_pct': round(custo_cpu, 2),
                'ram_gb': round(custo_ram, 3),
                'rede_mbps': round(custo_rede, 3),
            },
            'projecao': projecao,
        }

    # ---------------- 1. LEITURA DOS CSVs DA SILVER ----------------

    s3 = boto3.client('s3')  # Para teste local, comente esta linha
    servidores = defaultdict(dict)

    def registrar_linha_csv(linha):
        """Converte os campos de texto do CSV para numeros e agrupa por empresa, servidor e data."""
        try:
            empresa = int(linha['id_empresa'])
            servidor = int(linha['id_servidor'])
            data = datetime.fromisoformat(linha['data_hora'].replace('Z', '+00:00'))
            if data.tzinfo is None:
                data = data.replace(tzinfo=FUSO_LOCAL)
            data = data.astimezone(timezone.utc)

            # Converte os campos de texto do CSV para numeros e guarda com '_'
            linha['_data'] = data
            linha['_cpu'] = float(linha['cpu'])
            linha['_ram'] = float(linha['ram'])
            linha['_disco'] = float(linha['disco'])
            linha['_ram_total'] = float(linha['ram_total'])
            linha['_ram_disponivel'] = float(linha['ram_disponivel'])
            linha['_disco_total'] = float(linha['disco_total'])
            linha['_disco_disponivel'] = float(linha['disco_disponivel'])
            linha['_trocas_contexto'] = float(linha['trocas_contexto'])
            linha['_tempo_escrita_disco'] = float(linha['tempo_escrita_disco'])
            linha['_bytes_enviados'] = float(linha['bytes_enviados'])
            linha['_bytes_recebidos'] = float(linha['bytes_recebidos'])
            linha['_temperatura_cpu'] = float(linha['temperatura_cpu'])

            servidores[(empresa, servidor)][data] = linha
        except (KeyError, ValueError, TypeError):
            pass

    # --- MODO PRODUÇÃO (EC2 / S3): lê todas as coletas da Silver ---
    for pagina in s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=SILVER + 'metricas/'):
        for objeto in pagina.get('Contents', []):
            if not objeto['Key'].endswith('_dados.csv'):
                continue
            texto = s3.get_object(Bucket=BUCKET, Key=objeto['Key'])['Body'].read().decode('utf-8-sig')
            for linha in csv.DictReader(io.StringIO(texto)):
                registrar_linha_csv(linha)

    # --- MODO TESTE LOCAL (comente o bloco S3 acima e descomente abaixo para testar no PC) ---
    # arquivos_locais = ['dados_v1.csv', 'dados_v2.csv', 'dados_v3.csv']
    # for caminho in arquivos_locais:
    #     with open(caminho, mode='r', encoding='utf-8-sig') as arquivo:
    #         for linha in csv.DictReader(arquivo):
    #             registrar_linha_csv(linha)

    # ---------------- 2. JANELA DE TEMPO (24H) ----------------

    # MODO PRODUÇÃO (EC2): usa o relógio atual em UTC
    agora = datetime.now(timezone.utc)

    # MODO TESTE LOCAL (comente a linha acima e descomente abaixo para usar data fixa):
    # agora = datetime.fromisoformat("2026-11-21 16:40:00").replace(tzinfo=FUSO_LOCAL).astimezone(timezone.utc)

    inicio = agora - timedelta(hours=24)
    empresas = sorted({empresa for empresa, servidor in servidores})

    # ---------------- 3. PROCESSAMENTO POR EMPRESA E POR SERVIDOR ----------------

    lista_empresas_json = []

    for empresa in empresas:
        lista_servidores_json = []

        for (id_empresa, id_servidor), amostras in servidores.items():
            if id_empresa != empresa:
                continue

            # Coloca em ordem cronologica as capturas do servidor
            lista = sorted(amostras.values(), key=lambda x: x['_data'])
            ultima = lista[-1]  # Captura mais recente (minuto 10)
            penultima = lista[-2] if len(lista) >= 2 else None  # Minuto 9 (para diferenca de contadores)
            primeira_lote = lista[-10] if len(lista) >= 10 else lista[0]  # ~10 min atras (para crescimento de RAM)

            # Estruturas para guardar os pontos de 30 em 30 min (graficos de linha)
            pontos_30min = {}

            # Listas para guardar todas as amostras das ultimas 24h (calculo do Radar P95)
            hist_cpu_24h = []
            hist_ram_24h = []
            hist_disco_24h = []
            hist_rede_mbps_24h = []
            hist_temp_24h = []

            anterior = None
            rede_atual_mbps = 0.0

            for amostra in lista:
                data = amostra['_data']

                # Calcula a taxa de rede (Mbps) comparando o contador atual com o da captura anterior
                rede_mbps = 0.0
                if anterior:
                    segundos = (data - anterior['_data']).total_seconds()
                    enviados = amostra['_bytes_enviados'] - anterior['_bytes_enviados']
                    recebidos = amostra['_bytes_recebidos'] - anterior['_bytes_recebidos']
                    if 0 < segundos <= REDE_TIMEOUT and enviados >= 0 and recebidos >= 0:
                        rede_mbps = ((enviados + recebidos) * 8) / segundos / 1_000_000

                # Filtra apenas amostras dentro da janela das ultimas 24h
                if inicio <= data <= agora:
                    # Alimenta listas do Radar (P95)
                    hist_cpu_24h.append(amostra['_cpu'])
                    hist_ram_24h.append(amostra['_ram'])
                    hist_disco_24h.append(amostra['_disco'])
                    hist_temp_24h.append(amostra['_temperatura_cpu'])
                    if anterior:
                        hist_rede_mbps_24h.append(rede_mbps)

                    # Agrupa em blocos de 30 em 30 minutos (:00 ou :30) para os graficos de linha
                    data_local = data.astimezone(FUSO_LOCAL)
                    bloco_minuto = 0 if data_local.minute < 30 else 30
                    horario_bloco = data_local.replace(minute=bloco_minuto, second=0, microsecond=0)

                    pontos_30min[horario_bloco] = {
                        'cpu_pct': round(amostra['_cpu'], 1),
                        'ram_gb': round(amostra['_ram_total'] - amostra['_ram_disponivel'], 2),
                        'disco_mb': round((amostra['_disco_total'] - amostra['_disco_disponivel']) * 1024, 1),
                        'rede_mbps': round(rede_mbps, 2),
                    }

                anterior = amostra
                rede_atual_mbps = rede_mbps

            # Status e jogadores atuais
            atraso = (agora - ultima['_data']).total_seconds()
            status = 'offline' if atraso > OFFLINE_TIMEOUT else 'online'

            # Estima jogadores proporcionalmente ao uso de CPU
            jogadores_atuais = max(1, int(ultima['_cpu'] / 0.85))

            # 4. CÁLCULO DAS 4 KPIs (usando minuto 10 e minuto 9 / minuto 1)
            # KPI 1: Indice de Crescimento de Memoria (MB/h)
            ram_usada_agora_mb = (ultima['_ram_total'] - ultima['_ram_disponivel']) * 1024
            ram_usada_antes_mb = (primeira_lote['_ram_total'] - primeira_lote['_ram_disponivel']) * 1024
            minutos_decorridos = (ultima['_data'] - primeira_lote['_data']).total_seconds() / 60
            if minutos_decorridos > 0:
                crescimento_ram_mb_h = round(((ram_usada_agora_mb - ram_usada_antes_mb) / minutos_decorridos) * 60, 1)
            else:
                crescimento_ram_mb_h = 0.0

            # KPIs 2, 3 e 4: Diferenca dos contadores acumulados entre minuto 10 e minuto 9
            if penultima:
                seg_intervalo = max(1.0, (ultima['_data'] - penultima['_data']).total_seconds())
                trocas_s = int(max(0, ultima['_trocas_contexto'] - penultima['_trocas_contexto']) / seg_intervalo)
                fila_disco_ms = int(max(0, ultima['_tempo_escrita_disco'] - penultima['_tempo_escrita_disco']))

                bytes_totais = max(0, (ultima['_bytes_enviados'] - penultima['_bytes_enviados']) +
                                   (ultima['_bytes_recebidos'] - penultima['_bytes_recebidos']))
                bytes_s_jogador = round((bytes_totais / seg_intervalo) / jogadores_atuais, 1)
            else:
                trocas_s = 0
                fila_disco_ms = 0
                bytes_s_jogador = 0.0

            # 5. MONTAGEM DAS 4 LISTAS DO GRÁFICO CONSUMO DE RECURSOS 24H
            serie_cpu = []
            serie_ram = []
            serie_disco = []
            serie_rede = []
            for horario, valores in sorted(pontos_30min.items()):
                horario_str = horario.strftime('%Y-%m-%d %H:%M')
                serie_cpu.append({'horario': horario_str, 'valor': valores['cpu_pct']})
                serie_ram.append({'horario': horario_str, 'valor': valores['ram_gb']})
                serie_disco.append({'horario': horario_str, 'valor': valores['disco_mb']})
                serie_rede.append({'horario': horario_str, 'valor': valores['rede_mbps']})

            # 6. PROJEÇÃO DE ESCALABILIDADE DE JOGADORES
            ram_atual_gb = round(ultima['_ram_total'] - ultima['_ram_disponivel'], 2)
            escalabilidade = gerar_projecao_escalabilidade(
                jogadores_atuais, ultima['_cpu'], ram_atual_gb, rede_atual_mbps
            )

            # 7. ASSINATURA DE PERFORMANCE (RADAR 24H P95)
            rede_p95_mbps = calcular_p95(hist_rede_mbps_24h)
            rede_p95_pct = round(min(100.0, (rede_p95_mbps / CAPACIDADE_REDE_MBPS) * 100.0), 1)
            temp_p95_c = calcular_p95(hist_temp_24h)

            assinatura_performance = {
                'janela_analise': '24h_p95',
                'eixos': [
                    {'componente': 'RAM', 'valor_pct': calcular_p95(hist_ram_24h)},
                    {'componente': 'CPU', 'valor_pct': calcular_p95(hist_cpu_24h)},
                    {'componente': 'Disco', 'valor_pct': calcular_p95(hist_disco_24h)},
                    {'componente': 'Rede', 'valor_pct': rede_p95_pct},
                    {'componente': 'Eficiência térmica', 'valor_pct': calcular_eficiencia_termica(temp_p95_c)},
                ],
            }

            # Adiciona o objeto completo deste servidor na lista da empresa
            lista_servidores_json.append({
                'id_servidor': id_servidor,
                'nome': ultima.get('username') or f'Servidor {id_servidor}',
                'status': status,
                'ultima_coleta': ultima['data_hora'],
                'jogadores_atuais': jogadores_atuais,
                'kpis': {
                    'indice_crescimento_memoria': {
                        'valor_mb_h': crescimento_ram_mb_h,
                    },
                    'taxa_troca_contexto': {
                        'valor_trocas_s': trocas_s,
                    },
                    'tempo_fila_disco': {
                        'valor_ms': fila_disco_ms,
                    },
                    'eficiencia_payload': {
                        'valor_bytes_s_jogador': bytes_s_jogador,
                    },
                },
                'graficos': {
                    'consumo_recursos_24h': {
                        'cpu_pct': serie_cpu,
                        'ram_gb': serie_ram,
                        'disco_mb': serie_disco,
                        'rede_mbps': serie_rede,
                    },
                    'escalabilidade_jogadores': escalabilidade,
                    'assinatura_performance': assinatura_performance,
                },
            })

        # Guarda os servidores desta empresa na lista geral
        lista_empresas_json.append({
            'id_empresa': empresa,
            'servidores': sorted(lista_servidores_json, key=lambda x: x['id_servidor']),
        })

    # ---------------- 8. GRAVAÇÃO DO JSON ÚNICO NA CAMADA GOLD ----------------

    documento = {
        'gerado_em': agora.isoformat(),
        'empresas': lista_empresas_json,
    }

    # MODO PRODUÇÃO (EC2 / S3): sobrescreve o JSON único com todas as empresas no bucket Gold
    chave = f'{GOLD}dashboard_qa.json'
    s3.put_object(
        Bucket=BUCKET,
        Key=chave,
        Body=json.dumps(documento, ensure_ascii=False, indent=2),
        ContentType='application/json'
    )
    print(f'Gerado: {chave} ({len(lista_empresas_json)} empresas)')

    # MODO TESTE LOCAL (comente o bloco S3 acima e descomente abaixo para salvar local):
    # arquivo_saida = 'dashboard_qa.json'
    # with open(arquivo_saida, mode='w', encoding='utf-8') as f:
    #     json.dump(documento, f, ensure_ascii=False, indent=2)
    # print(f'Gerado localmente: {arquivo_saida} ({len(lista_empresas_json)} empresas)')

    print('Silver -> Gold concluído.')


if __name__ == '__main__':
    dash_felipe()