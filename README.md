# ETL Bronze -> Silver -> Gold

Requer Python 3 e `boto3`. Na EC2, o boto3 usa a IAM Role.

1. Configure `.env` a partir de `.env.example` e preencha `S3_BUCKET`.
2. Execute `python bronze2silver.py`.
3. Execute `python silver2gold.py`.

O Bronze é lido de `01-bronze/`, a Silver é gravada em `02-silver/metricas/` e a Gold em `03-gold/empresa-{id}/dash-geral.json`. Não existe `latest.json` em nenhum prefixo.

O primeiro script trata somente arquivos `_dados.csv`. A Silver é separada por empresa, servidor e data. O segundo lê apenas as partições `data=AAAA-MM-DD` dos últimos **40 dias** — o filtro é aplicado pela chave, antes de baixar o objeto — e grava, por empresa, o contrato `versao: 2` consumido pelo backend do site:

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

O acesso ao S3 usa a cadeia padrão de credenciais do `boto3`: perfil local (`AWS_PROFILE`) no teste ou IAM Role na Lambda/EC2. A Lambda precisa de `s3:ListBucket` no bucket, `s3:GetObject` em `02-silver/metricas/*` e `s3:PutObject` em `03-gold/*`.
