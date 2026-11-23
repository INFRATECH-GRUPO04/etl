# ETL Bronze -> Silver -> Gold

Requer Python 3 e `boto3`. Na EC2, o boto3 usa a IAM Role.

1. Configure `.env` a partir de `.env.example` e preencha `S3_BUCKET`.
2. Execute `python bronze2silver.py`.
3. Execute `python silver2gold.py`.

O Bronze é lido de `01-bronze/`, a Silver é gravada em `02-silver/metricas/` e a Gold em `03-gold/empresa-{id}/dash-geral.json`.

O primeiro script trata somente arquivos `_dados.csv`. A Silver é separada por empresa, servidor e data. O segundo gera, por empresa, o `dash-geral.json` com o contrato da dashboard geral: identificação, `conexao` (`online`/`offline`), última leitura, métricas atuais, disponibilidade por servidor e da frota. Campos como `situacao`, incidentes e relatório de IA ficam vazios e são preenchidos pelo backend do site.

Só aparecem servidores que já tenham enviado ao menos uma coleta para o Bronze. Sem histórico, a disponibilidade é `null` (não zero); com histórico, mas sem amostras na janela, é `0`.

Para testar a escrita do gold em um bucket pessoal, defina `S3_GOLD_BUCKET` no `.env`; quando não definido, usa-se `S3_BUCKET`. A frequência esperada de coleta (padrão: 60 segundos) pode ser ajustada com `FREQUENCIA_ESPERADA_SEGUNDOS`.

Execute `silver2gold.py` periodicamente para atualizar a `conexao` de servidores que parem de enviar dados.
