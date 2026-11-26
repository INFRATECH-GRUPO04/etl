# ETL Bronze -> Silver -> Gold

Requer Python 3 e `boto3`. O boto3 usa a cadeia padrão de credenciais: na EC2/Lambda, a IAM Role;
localmente, `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (ou `AWS_PROFILE`) do `.env`. Sem nada
preenchido, a role é assumida — não precisa de `if` no código. `AWS_REGION` (ou
`AWS_DEFAULT_REGION`) define a região; o padrão é `us-east-1`.

1. Configure `.env` a partir de `.env.example` e preencha `S3_BUCKET`.
2. Execute `python bronze2silver.py`.
3. Execute `python silver2gold.py`.

O Bronze é lido de `01-bronze/`, a Silver é gravada em `02-silver/metricas/` (`_dados.csv`) e `02-silver/processos/` (`_processos.csv`), e a Gold em `03-gold/empresa-{id}/dash-*.json`. Não existe `latest.json` em nenhum prefixo.

O primeiro script trata arquivos `_dados.csv` e `_processos.csv`, separados por empresa, servidor e data. O segundo lê apenas as partições `data=AAAA-MM-DD` dos últimos **40 dias** — o filtro é aplicado pela chave, antes de baixar o objeto — e grava, por empresa, seis arquivos principais: `dash-geral.json` (gestor), `dash-noc.json`, `dash-capacidade.json`, `dash-investigacao.json`, `dash-metricas.json` e `dash-operacional.json`, além das partições históricas em `dash-investigacao-historico/` e `dash-metricas/`. O `dash-geral.json` mantém o contrato `versao: 2` consumido pelo backend do site:

```json
{
  "versao": 2,
  "id_empresa": 2,
  "gerado_em": "2026-11-25T14:30:00-03:00",
  "servidores": [
    {
      "id_servidor": 3,
      "conexao": "online",
      "ultima_leitura": "2026-11-25T14:29:00-03:00",
      "atual": { "cpu": 93.4, "ram": 88.2, "disco": 71.5 }
    },
    {
      "id_servidor": 4,
      "conexao": "offline",
      "ultima_leitura": "2026-11-25T13:50:00-03:00",
      "atual": { "cpu": 41.2, "ram": 55.8, "disco": 63.1 }
    }
  ],
  "disponibilidade_frota": {
    "7d": {
      "rotulos": ["19/11", "20/11", "21/11", "22/11", "23/11", "24/11", "25/11"],
      "valores": [99.8, 99.9, 99.8, 99.8, 100.0, 99.8, 97.3]
    },
    "30d": {
      "rotulos": [
        "27/10", "28/10", "29/10", "30/10", "31/10",
        "01/11", "02/11", "03/11", "04/11", "05/11",
        "06/11", "07/11", "08/11", "09/11", "10/11",
        "11/11", "12/11", "13/11", "14/11", "15/11",
        "16/11", "17/11", "18/11", "19/11", "20/11",
        "21/11", "22/11", "23/11", "24/11", "25/11"
      ],
      "valores": [
        99.8, 99.9, 99.9, 99.9, 100.0, 99.8, 99.9, 99.9, 99.8, 99.9,
        99.8, 100.0, 99.9, 100.0, 99.8, 99.9, 99.9, 99.9, 99.7, 100.0,
        99.8, 99.9, 99.9, 99.8, 99.9, 99.8, 99.8, 100.0, 99.8, 97.3
      ]
    }
  }
}
```

A ETL calcula apenas o que depende das capturas: frescor (`conexao`/`ultima_leitura`), leituras atuais e disponibilidade da frota em 7d/30d. `situacao`, agregados de saúde e incidentes não entram no gold: a classificação pelos limites do MySQL e a rota complementar de incidentes ficam no backend do site.

A disponibilidade usa **dias de calendário** no fuso de `TIMEZONE`: 7 pontos em `7d` e 30 em `30d`, sempre rotulados `dd/mm` (o `7d` é o recorte dos últimos 7 pontos do `30d`). O dia de hoje é proporcional até o `gerado_em`. Por servidor, o esperado é o tempo monitorado dividido por `FREQUENCIA_ESPERADA_SEGUNDOS`; a frota de cada dia é **ponderada** por capturas recebidas ÷ esperadas (não é média simples dos percentuais). Antes da primeira captura do servidor o ponto é `null`; depois que ele já era monitorado e não enviou nada no dia, o ponto é `0`.

Só aparecem servidores que tenham ao menos uma coleta dentro da janela de 40 dias. Sem histórico, a disponibilidade é `null` (não zero); com histórico, mas sem amostras na janela, é `0`. Um servidor ativo no MySQL sem entrada no gold é contado como `offline` pelo backend.

Para testar a escrita do gold em um bucket pessoal, defina `S3_GOLD_BUCKET` no `.env`; quando não definido, usa-se `S3_BUCKET`. A frequência esperada de coleta (padrão: 60 segundos) pode ser ajustada com `FREQUENCIA_ESPERADA_SEGUNDOS`, e o fuso com `TIMEZONE` (padrão: `America/Sao_Paulo`).

Execute `silver2gold.py` periodicamente para atualizar a `conexao` de servidores que parem de enviar dados.

O acesso ao S3 usa a cadeia padrão de credenciais do `boto3`: variáveis de ambiente, perfil local
(`AWS_PROFILE`) ou IAM Role na Lambda/EC2. Preencha o par completo
(`AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`, e `AWS_SESSION_TOKEN` se for temporário) ou use
`AWS_PROFILE` com os arquivos compartilhados; deixar tudo comentado significa "usar a role". A
Lambda precisa de `s3:ListBucket` no bucket, `s3:GetObject` em `02-silver/metricas/*` e
`02-silver/processos/*`, e `s3:PutObject` em `03-gold/*`. As variáveis das Lambdas ficam na
configuração da AWS — o `.env` da EC2 não as altera.

## Deploy na AWS Lambda

Checklist das duas funções:

| Item | `bronze2silver` | `silver2gold` |
|---|---|---|
| Runtime | Python 3.11 | Python 3.11 (necessário por `zoneinfo`) |
| Handler | `bronze2silver.lambda_handler` | `silver2gold.lambda_handler` |
| Gatilho | evento do S3 em `01-bronze/` ou EventBridge a cada 10 min | EventBridge a cada 30 min |
| Timeout sugerido | 120–300 s | 300–600 s |
| Memória sugerida | 512 MB | 512–1024 MB |

As duas funções expõem `lambda_handler(evento, contexto)`; os dois arquivos também podem ser
executados diretamente com `python <arquivo>.py` para testes locais. O `boto3` já vem no runtime
da Lambda, então não precisa entrar no pacote. **Não inclua o `.env` no zip**: as variáveis da
Lambda são injetadas no ambiente e o `setdefault` dos scripts dá precedência a elas.

Variáveis na configuração da função:

- `S3_BUCKET` — obrigatória nas duas; sem ela, `run()` falha com erro explícito.
- `S3_BRONZE_PREFIX`, `S3_SILVER_PREFIX`, `S3_GOLD_PREFIX` — opcionais (padrões `01-bronze/`, `02-silver/`, `03-gold/`).
- `TIMEZONE`, `OFFLINE_TIMEOUT_SECONDS`, `FREQUENCIA_ESPERADA_SEGUNDOS`, `LIMITE_*_PCT`, `REDE_MAX_INTERVAL_SECONDS`, `LARGURA_BANDA_MBPS`, `PLANEJAMENTO_JANELA_DIAS`, `GOLD_RETENTION_DAYS`, `S3_GOLD_BUCKET` — opcionais, usadas pelo silver2gold.
- `AWS_REGION` é injetado automaticamente pela Lambda; `AWS_DEFAULT_REGION` é aceito como fallback. Não preencha credenciais AWS quando a função já tem IAM Role.

Política IAM mínima da role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::NOME_DO_BUCKET"
    },
    {
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": [
        "arn:aws:s3:::NOME_DO_BUCKET/01-bronze/*",
        "arn:aws:s3:::NOME_DO_BUCKET/02-silver/metricas/*",
        "arn:aws:s3:::NOME_DO_BUCKET/02-silver/processos/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": [
        "arn:aws:s3:::NOME_DO_BUCKET/02-silver/*",
        "arn:aws:s3:::NOME_DO_BUCKET/03-gold/*"
      ]
    }
  ]
}
```

`ListBucket` é necessário não só para a paginação, mas para o `ler_manifesto()` do silver2gold
conseguir distinguir `404` (chave ausente, normal) de `403` (permissão faltando). Não é preciso
`s3:DeleteObject`: a retenção de 180 dias apenas filtra a entrada, nunca apaga objetos do gold.

O bronze não usa watermark e reprocessa todo o prefixo `01-bronze/` a cada invocação, sobrescrevendo
as chaves da silver; isso está de acordo com o contrato atual. O dia gravado na partição `data=` vem
sempre dos 8 primeiros caracteres do lote no nome do arquivo, inclusive para lotes que cruzam
meia-noite.
