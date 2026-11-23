# Plano de implementação — ETL (`silver2gold.py`)

## 0. Como usar este documento em uma sessão nova

Este arquivo é **autossuficiente**: para implementar, basta ler este plano, o `AGENTS.md` da raiz do repo e o próprio `silver2gold.py`. Não é necessário histórico de conversa. As decisões da seção 3 são finais.

- Plano do repo irmão de site: `../InfraTech-SiteInstitucional/docs/plano-implementacao.md` (absoluto: `C:\Users\Luiz\Documents\-- SPTech repositories\InfraTech-SiteInstitucional\docs\plano-implementacao.md`). **O contrato de JSON precisa ser idêntico nos dois documentos; se um mudar, o outro muda junto.**
- Escopo: só este repo. Não mexer no site nem no script de captura.
- Sem teste automatizado, lint ou CI. Validação é rodar os scripts e baixar o JSON do S3.

Estado atual de `silver2gold.py` (167 linhas, conferido em 22/11/2026):

- linhas 10–15: lê `.env` se existir; na EC2 o boto3 usa IAM Role.
- linhas 17–25: `BUCKET`, `SILVER`, `GOLD`, `OFFLINE_TIMEOUT=900`, `REDE_TIMEOUT=30`, `LIMITE_CPU/RAM/DISCO=80`, `FUSO_LOCAL=America/Sao_Paulo`.
- linhas 31–50: lista `02-silver/metricas/*_dados.csv`; normaliza `_data` (UTC), `_cpu`, `_ram`, `_disco` e guarda em `servidores[(empresa, servidor)][data] = linha`.
- linhas 52–54: `agora` (UTC), `inicio = agora - 24 h`, `empresas` ordenadas.
- linhas 56–138: por empresa, monta `lista` (amostras ordenadas), `ultima = lista[-1]`, `taxas`/`recursos`/`anterior` (rede e série por minuto), `status` (offline/atencao/online), `grafico_recursos`, `metricas_atuais` e `atuais[]`.
- linhas 140–156: monta `documento` com `kpis`, contagens de rede e `servidores`.
- linhas 158–165: grava `03-gold/empresa-{empresa}/dashboard.json` com `Bucket=BUCKET`.
- `.env.example` hoje traz `S3_BUCKET`, os prefixos, `OFFLINE_TIMEOUT_SECONDS`, `REDE_MAX_INTERVAL_SECONDS` e `LIMITE_*_PCT` — os três últimos saem.
- `README.md` cita `servidores_atual.csv`, `historico_24h.csv` e `alertas_ativos.csv`, mas **`silver2gold.py` não gera nenhum desses CSVs**: são texto antigo de demonstração. Não tentar manter nem criar.
- Execução: Python 3 + `boto3`, `.env` configurado, `python bronze2silver.py` e depois `python silver2gold.py`. `bronze2silver.py` não muda.

---

## 1. Objetivo e escopo

Ajustar `silver2gold.py` para gerar o JSON consumido pela dashboard geral do site. Hoje o script já lê a silver e gera `03-gold/empresa-{id}/dashboard.json` com um formato diferente; a meta é trocar esse documento pelo contrato combinado com o site.

Os campos que hoje aparecem nesse documento (`kpis`, `status`, `metricas_atuais` em separado, `graficos`, tráfego de rede etc.) eram exemplos para demonstrar a ETL ao cliente. **Nenhum deles precisa ser mantido**: o JSON de saída terá somente o que a dash geral usa, conforme o contrato da seção 4.

Escopo: **apenas este repositório de ETL**. O site tem plano próprio em `InfraTech-SiteInstitucional/docs/plano-implementacao.md` e o script de captura não muda. Os dois repos se acoplam somente pelo arquivo no S3; nenhum código é compartilhado.

Regra geral: **aproveitar o que o script já faz**. Não criar nova camada, não adicionar biblioteca, não mudar `bronze2silver.py`.

---

## 2. Situação atual

`silver2gold.py` (167 linhas):

- lê todos os `02-silver/metricas/*_dados.csv` com `boto3`;
- agrupa em `servidores[(empresa, servidor)][datetime] = linha`;
- por empresa, monta `kpis`, `servidores[]` com `status`, `ultima_coleta`, `metricas_atuais` e `graficos.recursos_24h` (última captura de cada minuto);
- grava `03-gold/empresa-{empresa}/dashboard.json`.

O `status` de hoje (`online`/`atencao`/`offline`) usa `LIMITE_CPU_PCT`, `LIMITE_RAM_PCT`, `LIMITE_DISCO_PCT` e `OFFLINE_TIMEOUT_SECONDS`. Essa classificação vai sair daqui: **quem decide `situacao` é o backend do site, comparando com os limites do MySQL**. A ETL fica só com `conexao` (frescor da coleta).

---

## 3. Decisões

| Ponto | Decisão |
| --- | --- |
| Arquivo de saída | `03-gold/empresa-{id}/dash-geral.json` (substitui `dashboard.json`, que não tem consumidor hoje) |
| Classificação | ETL informa apenas `conexao` (`online`/`offline`) com `OFFLINE_TIMEOUT_SECONDS`; backend calcula `situacao` |
| Série por servidor | **Removida.** `serie_24h` não é lida pela dash-geral; o script deixa de montar `grafico_recursos` |
| Campos do site | ETL deixa `nome`, `cidade`, `tempo_min`, `situacao`, `inc7d`, `incidentes` e `relatorio_ia` vazios; o backend preenche |
| Conteúdo | Somente o contrato da dash geral. Nada do documento de teste antigo é preservado, nem o `dashboard.json` |
| Bucket de teste | `S3_GOLD_BUCKET`, se definido, substitui `S3_BUCKET` apenas na escrita do gold — permite apontar para um bucket pessoal |
| Dependências | Nenhuma nova — `boto3` já é usado |
| `bronze2silver.py` | Não muda |

---

## 4. Contrato de saída

Mesmo formato do site, gerado por empresa:

```json
{
  "versao": 1,
  "id_empresa": 2,
  "gerado_em": "2026-11-21T13:10:00+00:00",
  "ambiente": null,
  "servidores": [
    {
      "id_servidor": 4,
      "nome": null,
      "cidade": null,
      "conexao": "online",
      "ultima_leitura": "2026-11-21T13:08:00+00:00",
      "tempo_min": null,
      "situacao": null,
      "inc7d": null,
      "atual": { "cpu": 95, "ram": 87, "disco": 82 },
      "disponibilidade_pct": { "24h": 99.2 }
    }
  ],
  "disponibilidade_frota": {
    "24h": { "rotulos": [], "valores": [], "media": null, "anterior": null },
    "7d":  { "rotulos": [], "valores": [], "media": null, "anterior": null },
    "30d": { "rotulos": [], "valores": [], "media": null, "anterior": null }
  },
  "incidentes": {
    "abertos": [],
    "por_dia": {
      "7d":  { "rotulos": [], "valores": [], "anterior": null },
      "30d": { "rotulos": [], "valores": [], "anterior": null }
    },
    "mttr": { "minutos": null, "periodo": "(últimos 30d)" }
  },
  "relatorio_ia": {
    "gerado_em": null,
    "resumo": "",
    "pontos": [],
    "recomendacoes": []
  }
}
```

`ambiente` fica `null` porque o backend usa `"Produção"`. As chaves sempre existem, mesmo vazias — o front não faz verificação de existência. `serie_24h` e as janelas 7d/30d por servidor não estão no contrato: a dash-geral não as lê.

---

## 5. Origem dos campos

| Campo | Origem na ETL |
| --- | --- |
| `versao`, `id_empresa`, `gerado_em` | constante `1`, empresa do loop, `agora.isoformat()` |
| `atual` | última captura (`cpu`, `ram`, `disco`) |
| `conexao` | `offline` se `atraso > OFFLINE_TIMEOUT_SECONDS`, senão `online` |
| `ultima_leitura` | timestamp da última captura em ISO |
| `disponibilidade_pct` | razão entre capturas recebidas e esperadas nas últimas 24 h |
| `disponibilidade_frota` | média da disponibilidade dos servidores por bucket |
| demais campos | `null`, `""` ou estrutura vazia |

---

## 6. Regras de cálculo

Adicionar no topo do script:

```python
FREQUENCIA = int(os.getenv('FREQUENCIA_ESPERADA_SEGUNDOS', '60'))
JANELAS = {'24h': 86400, '7d': 7 * 86400, '30d': 30 * 86400}
```

Helper único para disponibilidade (cerca de 5 linhas):

```python
def disponibilidade(lista, inicio, fim):
    if not lista:
        return None
    recebidas = sum(1 for a in lista if inicio <= a['_data'] < fim)
    esperadas = (fim - inicio).total_seconds() / FREQUENCIA
    return round(min(100, recebidas / esperadas * 100), 1) if esperadas > 0 else None
```

- `disponibilidade_pct['24h']` = `disponibilidade(lista, agora - timedelta(hours=24), agora)`. As janelas 7d/30d continuam existindo apenas em `disponibilidade_frota`.
- **`None` (null no JSON) significa "sem dados"**, nunca `0` falso. Se o servidor tem histórico mas nenhuma amostra na janela, aí sim o resultado é `0`.
- `disponibilidade_frota[periodo]`:
  - `24h`: 24 buckets de 1 hora, rótulo `%Hh`;
  - `7d`: 7 buckets de 1 dia, rótulos `Seg, Ter, Qua, Qui, Sex, Sáb, Dom` (`weekday()`);
  - `30d`: 30 buckets de 1 dia, rótulo `%d/%m`.
  - Cada `valores[i]` é a média dos valores não nulos da disponibilidade dos servidores naquele bucket (usando o mesmo helper com o intervalo do bucket). Sem nenhum valor, `valores[i]` é `null`.
  - `media` = média dos valores não nulos; `anterior` = mesma média na janela imediatamente anterior (deslocar `agora` de uma janela inteira). Se não houver valores, ambos ficam `null`.

Isso segue a regra do `AGENTS.md`: sem dados, a dash mostra "sem dados", nunca zero falso.

---

## 7. Mudanças no `silver2gold.py`

1. Remover `LIMITE_CPU`, `LIMITE_RAM`, `LIMITE_DISCO`, `REDE_TIMEOUT`, o cálculo de `taxas` (upload/download) e os totais de rede. Adicionar `FREQUENCIA`, `JANELAS` e `GOLD_BUCKET = os.getenv('S3_GOLD_BUCKET') or BUCKET` (o `or` evita bucket vazio se a variável existir sem valor). Manter `OFFLINE_TIMEOUT` e `FUSO_LOCAL`.
2. Adicionar o helper `disponibilidade()` e um helper curto `serie_frota(listas, segundos, passos, rotulo)` que devolve `{rotulos, valores, media, anterior}`.
3. Dentro do loop `for (id_empresa, id_servidor), amostras ...`:
   - manter apenas `lista`, `ultima` e os valores `cpu/ram/disco`; remover `recursos`, `grafico_recursos`, `anterior`, `taxas` e `metricas_atuais`;
   - trocar o bloco de `status` por `conexao = 'offline' if atraso > OFFLINE_TIMEOUT else 'online'`;
   - montar `atual` diretamente dos valores da última captura e `disponibilidade_pct` com `24h`;
   - guardar `lista` em uma variável local da empresa para o cálculo da frota;
   - montar o servidor com as chaves do contrato, usando `None`/`""`/`[]` nos campos do backend.
4. Depois do loop: calcular `disponibilidade_frota` e montar o `documento` novo. **Não gerar `kpis`, CSVs nem campos de rede** — nada disso vai para a dash geral.
5. Trocar a chave gravada: `f'{GOLD}empresa-{empresa}/dash-geral.json'` e o `Bucket=GOLD_BUCKET` no `put_object`.
6. `.env.example`: remover `LIMITE_*_PCT` e `REDE_MAX_INTERVAL_SECONDS`; adicionar `FREQUENCIA_ESPERADA_SEGUNDOS=60` e `S3_GOLD_BUCKET` (opcional, com comentário de que serve para testar em bucket pessoal).
7. `README.md`: atualizar as linhas 9 e 11 para falar de `dash-geral.json` e do contrato novo, e mencionar `S3_GOLD_BUCKET`.
8. `AGENTS.md` é compartilhado entre os três repositórios; **não alterar** neste plano (a nota sobre `limite_alerta_global` fica como pendência documental).

Tamanho estimado: cerca de 30 linhas adicionadas e 70 removidas em `silver2gold.py` — o script fica visivelmente menor.

---

## 8. Fora de escopo

- Incidentes e Jira: ficam vazios; o backend do site preenche.
- Relatório de IA: fica vazio; o backend gera o template.
- `nome`/`cidade`: vêm do MySQL no site; a silver não tem localização confiável.
- `situacao`: não é mais calculada aqui; `conexao` é o único estado operacional da ETL.
- Campos do documento antigo que **não voltam**: `kpis`, `status`, `ultima_coleta`, `metricas_atuais`, `graficos`, tráfego de rede e os CSVs `servidores_atual.csv` / `historico_24h.csv` / `alertas_ativos.csv`.
- `serie_24h` e `disponibilidade_pct` em 7d/30d por servidor também ficam de fora: são campos do rascunho original, mas a dash-geral não os consome.
- Nenhum CSV auxiliar entra nesta entrega: o `silver2gold.py` atual não gera `servidores_atual.csv`, `historico_24h.csv` nem `alertas_ativos.csv`, apesar do que diz o `README.md`.

---

## 9. Ordem de execução e validação

1. Aplicar as mudanças de `silver2gold.py`, `.env.example` e `README.md`.
2. Rodar `python bronze2silver.py` e depois `python silver2gold.py` com `S3_BUCKET`, `S3_GOLD_BUCKET` (se for usar bucket pessoal) e `FREQUENCIA_ESPERADA_SEGUNDOS` configurados.
3. Baixar `03-gold/empresa-{id}/dash-geral.json` e conferir: chaves do contrato presentes, `servidores` com `atual`/`disponibilidade_pct.24h`, e `incidentes`/`relatorio_ia` vazios. Conferir também que nem campos de teste antigos (`kpis`, rede, `status`) nem `serie_24h` apareceram.
4. Se o servidor não tiver captura nenhuma, conferir que a disponibilidade é `null` e não `0`.
5. Conferir um servidor parado há mais de `OFFLINE_TIMEOUT_SECONDS`: `conexao = "offline"` e disponibilidade menor na janela correspondente.
6. Com o backend do site implementado, abrir a dash geral e confirmar que KPIs, gráficos e tabela são preenchidos com os mesmos números do JSON.
