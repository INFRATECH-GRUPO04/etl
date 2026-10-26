# ETL Bronze -> Silver -> Gold

Requer Python 3 e `boto3`. Na EC2, o boto3 usa a IAM Role.

1. Configure `.env` a partir de `.env.example` e preencha `S3_BUCKET`.
2. Execute `python bronze2silver.py`.
3. Execute `python silver2gold.py`.

O Bronze é lido de `01-bronze/`, a Silver é gravada em `02-silver/metricas/` e a Gold em `03-gold/empresa_id=<id>/`.

O primeiro script trata somente arquivos `_dados.csv`. A Silver é separada por empresa, servidor e data. O segundo gera `servidores_atual.csv`, `historico_24h.csv` e `alertas_ativos.csv` por empresa.

Só aparecem servidores que já tenham enviado ao menos uma coleta para o Bronze. Os status são `online`, `atencao` e `offline`.

Execute `silver2gold.py` periodicamente para atualizar o status de servidores que parem de enviar dados.
