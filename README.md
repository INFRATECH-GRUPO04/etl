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
  "gerado_em": "2026-11-25T13:10:00-03:00",
  "servidores": [
    {
      "id_servidor": 1,
      "conexao": "online",
      "ultima_leitura": "2026-11-25T13:08:00-03:00",
      "atual": { "cpu": 95, "ram": 50, "disco": 40 }
    }
  ],
  "disponibilidade_frota": {
    "7d": { "rotulos": ["Sex", "Sáb", "Dom"], "valores": [99.1, null, 98.7] },
    "30d": { "rotulos": ["26/10", "27/10"], "valores": [99.3, null] }
  }
}
```

A ETL calcula apenas o que depende das capturas: frescor (`conexao`/`ultima_leitura`), leituras atuais e disponibilidade da frota em 7d/30d. `situacao`, agregados de saúde, incidentes e relatório de IA não entram no gold: a classificação pelos limites do MySQL e as rotas complementares ficam no backend do site.

Só aparecem servidores que tenham ao menos uma coleta dentro da janela de 40 dias. Sem histórico, a disponibilidade é `null` (não zero); com histórico, mas sem amostras na janela, é `0`. Um servidor ativo no MySQL sem entrada no gold é contado como `offline` pelo backend.

Para testar a escrita do gold em um bucket pessoal, defina `S3_GOLD_BUCKET` no `.env`; quando não definido, usa-se `S3_BUCKET`. A frequência esperada de coleta (padrão: 60 segundos) pode ser ajustada com `FREQUENCIA_ESPERADA_SEGUNDOS`, e o fuso com `TIMEZONE` (padrão: `America/Sao_Paulo`).

Execute `silver2gold.py` periodicamente para atualizar a `conexao` de servidores que parem de enviar dados.

O acesso ao S3 usa a cadeia padrão de credenciais do `boto3`: perfil local (`AWS_PROFILE`) no teste ou IAM Role na Lambda/EC2. A Lambda precisa de `s3:ListBucket` no bucket, `s3:GetObject` em `02-silver/metricas/*` e `s3:PutObject` em `03-gold/*`.
