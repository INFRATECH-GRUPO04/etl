# Diário de Prompt

## 2026-11-22

### Prompt 1 - etl

**Enviado:**
> tem um arquivo na main de um repositório, como posso passar esse arquivo para minha branch?

**Resultado da IA:**
- Identificou que a branch atual era `feat/dados-dashboard-sarah`.
- Orientou usar `git restore --source=main -- caminho/do/arquivo` para trazer um arquivo específico da branch `main` para a branch atual.
- Explicou que, depois de trazer o arquivo, é preciso `git add` e `git commit` para registrar a alteração.
- Apresentou `git checkout main -- caminho/do/arquivo` como alternativa equivalente, usando a sintaxe antiga do Git.
- Para trazer todas as alterações da `main`, indicou as opções `git merge main` e `git rebase main`.

## 2026-11-23

### Prompt 1 - etl

**Enviado:**
> cada pessoa do meu grupo vai fazer um json para as dashboards individuais, eu estou alterando o script silver2gold para criar o me json (dashboard_sarah.json)
> ainda vou alterar, mas o dashboard.json não precisa mais existir, me indique tudo que o dashboard.json usa, mas o dashboard_sarah.json não utiliza
> para que eu possa excluir tudo que não preciso

**Resultado da IA:**
- Configs exclusivas do dashboard: `OFFLINE_TIMEOUT`, `REDE_TIMEOUT`, `LIMITE_CPU`, `LIMITE_RAM`, `LIMITE_DISCO` (`silver2gold.py:20-24`) e as 5 chaves em `.env.example:13-18`; também `inicio` (`:53`) e o import de `timedelta` (`:5`).
- Código exclusivo: loop por empresa (`:54-56`), `upload_atual`/`download_atual` (`:58`, `:99-102`), `taxas` (`:65`, `:77-89`, `:99`), `recursos` (`:66`, `:71`, `:74-75`), `atraso`/`status` (`:91-97`), `grafico_recursos` (`:104-112`), `metricas_atuais` (`:114-127`), `atuais` (`:57`, `:129-138`), contadores `online/atencao/offline` (`:140-142`), `documento`/`kpis` (`:143-156`) e o `put_object` do `dashboard.json` (`:158-165`).
- Campos que só existem no `dashboard.json`: `kpis` (`total_servidores`, `servidores_online`, `servidores_atencao`, `servidores_offline`, `upload_mbps`, `download_mbps`, `trafego_mbps`), `status`, `nome`, `ultima_coleta`, `id_servidor`, `metricas_atuais.*` e `graficos.recursos_24h.*`.
- Alerta dado: o trecho do Sarah ainda dependia de `lista`, `id_empresa`, `id_servidor` e `agora` deixados pelo loop do dashboard; também foi notada a divergência de `idEmpresa`/`idServidor` e do formato de `lote` em relação a `perfeito-sarah.json`.

### Prompt 2 - etl

**Enviado:**
> exclua tudo que faz parte somente do dashboard.json

**Resultado da IA:**
- `silver2gold.py` reescrito com apenas o fluxo do `dashboard_sarah.json`, gerado por `(id_empresa, id_servidor)`.
- Removidas as 5 variáveis de monitoramento de `.env.example`.
- Ajustes necessários após remover o loop antigo: `lista` agora é montada dentro do loop de servidores, e o `put_object` saiu de dentro do loop de variação (antes gravava o JSON uma vez por captura).
- Validação: `python -m py_compile silver2gold.py` terminou sem erros; o `__pycache__` gerado foi removido.

### Prompt 3 - etl

**Enviado:**
> gere um arquivo "diario_sarah.md" no repositório, com relatório dos prompts enviados e o resultado que a IA me retornou.
> É possível atualizar esse arquivo toda vez que eu mandar um prompt?
> Pode ter um título "Diário de Prompt"
> separar por dia e prompt 1, prompt 2, e assim por diante

**Resultado da IA:**
- Arquivo `diario_sarah.md` criado com o título "Diário de Prompt", seções por data e entradas numeradas contendo o prompt enviado e o resumo da resposta.
- Adicionada instrução persistente em `AGENTS.md` (seção 8, nota do ETL) para que as próximas sessões registrem cada prompt no diário sem apagar as entradas anteriores.
- Explicação: `AGENTS.md` é carregado como instrução do projeto e alterações nele são detectadas antes do próximo prompt (documentação: https://opencode.ai/docs/rules/). Para automação literal independente do agente, seria necessário um plugin/hook do OpenCode.

### Prompt 4 - etl

**Enviado:**
> formate o prompt 1 do dia 22/11/2026 da mesma forma que o restante dos relatórios estão

**Resultado da IA:**
- Reformatação do `Prompt 1 - etl` de `2026-11-22` em `diario_sarah.md`: o "Resultado da IA" passou de parágrafos soltos para uma lista de marcadores, com comandos e nomes de branch entre crases, igual aos demais prompts.
- Este prompt também foi registrado aqui, conforme a regra de atualização do diário.

### Prompt 5 - etl

**Enviado:**
> insira no json a media de cada componente dos dados do csv

**Resultado da IA:**
- Adicionado o campo `medias` ao `dashboard_sarah.json` gerado por `silver2gold.py`, com a média de `cpu`, `ram`, `disco` e `rede` calculada sobre as capturas do lote (`capturas`), arredondada a 1 casa decimal.
- Valores `None` (rede sem captura anterior, contador zerado ou intervalo grande) são ignorados na média; se não houver valores válidos, a média fica `None`.
- Validação: `python -m py_compile silver2gold.py` sem erros; `__pycache__` removido.

### Prompt 6 - etl

**Enviado:**
> de um exemplo de um arquivo json gerado pelo script silver2gold

**Resultado da IA:**
- Mostrado um exemplo de `dashboard_sarah.json` no formato atual do script, com `id_empresa`, `id_servidor`, `nome_servidor`, `lote`, `gerado_em`, `medias` e `capturas` (`dataHora`, `cpu`, `ram`, `disco`, `rede`, `variacao`).
- Exemplo com 3 capturas para caber na resposta; o padrão do script é até 10 (`CAPTURAS_SARAH`).

### Prompt 7 - etl

**Enviado:**
> altere o diário para que indique o repositório que foi enviado o prompt
> pode ser:
> prompt 1 - etl

**Resultado da IA:**
- Todos os títulos de entradas de `diario_sarah.md` passaram a incluir o repositório no formato `Prompt N - etl`, mantendo a numeração por dia.
- A referência ao prompt de `2026-11-22` dentro do `Prompt 4 - etl` também foi atualizada para o novo formato.
- Regra em `AGENTS.md` ajustada para exigir o sufixo com o nome do repositório nas próximas entradas.

### Prompt 8 - etl

**Enviado:**
> sabendo que o prefixo 02-silver tem os seguintes arquivos metricas -> empresa_id=x -> servidor_id=y -> data=yyyy-mm-dd -> csvs do dia
> minha tela tem um filtro de data início e data final, de forma que define o intervalo dos dados que aparecem na dashboard
> eu preciso calcular por exemplo a saúde do servidor nesse período, ou a média do dia para adicionar em um gráfico de linha de uso de recursos, a saúde durante os períodos do dia (manhã, tarde, noite, madrugada)
> ou seja, os dados que eu analisaria seria maior que o csv do lote e até das pastas de datas
> como posso fazer isso?
> tem alguma conta que eu poderia fazer no arquivo silver2gold ou seria ideal puxar os dados e tratar no js da página?

**Resultado da IA:**
- Recomendou não agregar no JS da página nem gerar um JSON por intervalo no ETL; sugeriu camadas: Gold com séries compactas/rollups por dia e período, back-end Node filtrando o intervalo e aplicando os limites do MySQL, front só renderizando.
- Observou que o particionamento `data=YYYY-MM-DD` permite listar só os dias do filtro, e que agregados precisam guardar `n` (quantidade de amostras) para médias ponderadas corretas.
- Alertou que "saúde" depende dos limites configurados por empresa/servidor no MySQL (não das env vars antigas do ETL) e que o critério de offline histórico deve ser baseado em intervalo entre coletas, não em `agora - última coleta`.

### Prompt 9 - etl

**Enviado:**
> sabendo que uma pasta de servidor_id=x tem vários csv com 10 capturas cada, o json deve ter informações dos csvs de 1 dia ou cada csv gera 1 json?

**Resultado da IA:**
- Recomendou 1 JSON por `(servidor, dia)`, agregando todos os CSVs da pasta `data=YYYY-MM-DD`, e não 1 JSON por CSV.
- Justificativa: o CSV é unidade de transporte do lote (10 min), enquanto o filtro da tela e os gráficos são diários; um JSON por lote geraria ~144 objetos/dia e exigiria centenas de leituras por consulta.
- Sugeriu manter o `dashboard_sarah.json` atual como "último lote" e criar à parte o JSON do dia com a série completa e/ou médias por período, com deduplicação por `data_hora` antes de agregar.

### Prompt 10 - etl

**Enviado:**
> o script silver2gold lê todos os arquivos dentro da pasta de datas?

**Resultado da IA:**
- Confirmou que sim: `list_objects_v2` com `Prefix=SILVER + 'metricas/'` (`silver2gold.py:26`) lista recursivamente todos os objetos sob `02-silver/metricas/`; no S3 as "pastas" são apenas prefixos da chave.
- O único filtro aplicado é o sufixo `_dados.csv` (`:28`) — não há filtro de data, empresa ou servidor; todas as linhas são carregadas em `servidores` (`:43`).
- Observou que o dicionário é chaveado por `data_hora`, então capturas com o mesmo instante são sobrescritas, e que apenas as últimas `CAPTURAS_SARAH` chegam ao JSON.

### Prompt 11 - etl

**Enviado:**
> alterei a chave_sarah para os caminhos que eu falei previamente, está errado?

**Resultado da IA:**
- Verificou a nova chave em `silver2gold.py:120`: `03-gold/empresa_id=<id>/servidor_id=<id>/dashboard_sarah.json`.
- Avaliou como chave S3 válida e mais consistente com `bronze2silver.py:56` e com o README, porém incompleta para o filtro de datas: sem o nível `data=`/`dia=`, o arquivo é sobrescrito a cada execução e guarda só o último lote.
- Recomendou manter esse caminho para o "último lote" e gravar à parte um objeto por servidor/dia, além de alinhar o prefixo ao consumidor (back-end Node/Java), que não está neste repositório.

### Prompt 12 - etl

**Enviado:**
> adicione o caminho da data porfavor

**Resultado da IA:**
- Incluído o nível `data=` em `chave_sarah` (`silver2gold.py:120`), usando `data_arquivo = capturas[-1]['dataHora'][:10]` (data local da última captura do lote).
- Chave resultante: `03-gold/empresa_id=<id>/servidor_id=<id>/data=YYYY-MM-DD/dashboard_sarah.json`, igual ao padrão de partição usado na Silver.
- Validação: `python -m py_compile silver2gold.py` sem erros; `__pycache__` removido.

### Prompt 13 - etl

**Enviado:**
> essa data_arquivo vem de onde?
> porque tem que ser o horário da primeira captura do csv

**Resultado da IA:**
- Explicou que `data_arquivo` vinha de `capturas[-1]['dataHora'][:10]`, ou seja, da data local da última captura do lote; `dataHora` é montada em `:85` a partir de `_data` (linha `data_hora` do CSV, convertida para `America/Sao_Paulo`).
- Atendido o pedido: alterado para `capturas[0]['dataHora'][:10]` (`silver2gold.py:120`), usando a data da primeira captura do lote.
- Validação: `python -m py_compile silver2gold.py` sem erros; `__pycache__` removido.

### Prompt 14 - etl

**Enviado:**
> da forma que está o arquivo silver2gold, a primeira captura retorna uma variação nula, pois não tem anterior
> mas o anterior seria a ultima captura do csv anterior, é possível buscar a ultima captura do csv anterior para calcular a variãção?

**Resultado da IA:**
- Explicou que não é preciso nova leitura do S3: o script já carrega todo o histórico da Silver em `servidores`, então a captura anterior ao lote já está em memória.
- Refatorou o trecho das capturas em `silver2gold.py`: `recorte` agora pega `CAPTURAS_SARAH + 2` amostras, monta `pontos` com `rede` calculada para cada uma e emite as últimas `CAPTURAS_SARAH` como `capturas`.
- A variação da primeira captura passou a usar `pontos[indice_anterior]`, que inclui a última captura do lote anterior; com apenas uma amostra anterior disponível, `variacao.rede` fica `None` (não há como calcular a rede dessa referência).
- Validação: teste com S3 simulado (20 capturas) gerou 10 capturas com `variacao.cpu = -9.0` na primeira, conferindo a diferença contra a captura 20:09; `py_compile` sem erros.

### Prompt 15 - etl

**Enviado:**
> o que a linha gerado_em faz?
> porque o lote já mostra de quando é o csv

**Resultado da IA:**
- Explicou que `gerado_em` (`silver2gold.py:113`) registra o instante em que a Lambda gerou o JSON (`agora = datetime.now(timezone.utc)`, em `:47`), enquanto `lote` (`:112`) é o horário da última captura do CSV, no fuso local.
- Esclareceu que não são redundantes: `lote` é a origem dos dados e `gerado_em` é auditoria do pipeline (detectar JSON parado, medir atraso, desempatar reprocessamentos). Nenhum cálculo depende de `gerado_em`; pode ser removido se o front não usar.

### Prompt 16 - site institucional

**Enviado:**
> já leu o AGENTS.md?

**Resultado da IA:**
- Confirmei a leitura do `AGENTS.md`, incluindo as notas específicas do repositório do site.

### Prompt 17 - site institucional

**Enviado:**
> perfeito!
> gostaria de saber como conectar a minha dashboard na aws s3
> comecei a estruturar o fetch e o .env e o .env.dev
> preciso fazer as rotas
> é a mesma estrutura que as rotas do banco de dados?

**Resultado da IA:**
- Confirmado que a estrutura é a mesma (`routes → controllers → models`), trocando `database.executar()` por chamadas ao SDK S3, já instalado em `package.json:13` e usado em `src/controllers/agenteController.js:10`.
- Sugeridas rotas autenticadas em `app.js` com `autenticar` e `exigirPermissao('monitorar')`, usando `req.usuario.empresa` em vez de ID recebido do front.
- Indicados `ListObjectsV2Command`/`GetObjectCommand` com `transformToString()`, `AWS_REGION` e `S3_BUCKET` já presentes em `.env.dev` (faltando `AWS_REGION` no `.env.example`), credenciais fora do front e `204` para "sem dados".
- Perguntado como as Lambdas nomeiam os objetos em `03-gold/`, para filtrar por empresa sem vazar dados de outra empresa.

### Prompt 18 - site institucional

**Enviado:**
> as pastas estão estruturadas da seguinte forma:
> 03-gold/metricas/empresa_id=2/servidor_id=3/data=2026-11-10/20261110T123918Z_empresa-2_servidor-3_dados.csv
>
> onde em metricas são listadas todas as empresas que usufruem do nosso sistema, em empresa são listados os servidores da empresa, dentro de servidor tem as datas de capturas, dentro de data são armazenados os csv do dia em questão com 10 registros cada csv

**Resultado da IA:**
- Montado o prefixo `03-gold/metricas/empresa_id=${req.usuario.empresa}/servidor_id=${id}/data=${yyyy-mm-dd}/`, com `Delimiter: '/'` para descobrir `servidor_id=`/`data=` e `ContinuationToken` para paginação acima de 1000 objetos.
- Observado que o Gold guarda CSV (não JSON como diz o `AGENTS.md`), com o cabeçalho definido em `agenteController.js:14`.
- Sugeridas as rotas `/dashboard-dados/servidores` (via MySQL, reaproveitando `servidorModel.listar`/`listarPorFuncionario`), `/dashboard-dados/historico/:idServidor?inicio=&fim=` e `/dashboard-dados/resumo`.
- Reforçado o isolamento por `req.usuario.empresa` e validação do servidor com `servidorPertenceEmpresa`, respondendo `204` quando não houver objetos.
- Perguntado se o front precisa do histórico completo por servidor ou apenas da última leitura antes de criar os arquivos.

### Prompt 19 - site institucional

**Enviado:**
> salve os prompts que mandei aqui no diário_sarah.md

**Resultado da IA:**
- Criado `diario_sarah.md` na raiz do repositório do site com os três prompts enviados até então.
- Em seguida o usuário direcionou os registros para o diário do repositório ETL e pediu a remoção do arquivo criado no site.

### Prompt 20 - site institucional

**Enviado:**
> no diário do repositório etl
> utilize o arquivo já criado

**Resultado da IA:**
- Registradas as entradas acima no arquivo existente `etl/diario_sarah.md`, continuando a numeração de `2026-11-23` a partir do Prompt 15, sem apagar as entradas anteriores.
- Após correção do usuário, o sufixo das entradas 16 a 20 passou a indicar o repositório de origem (`site institucional`); as entradas 1 a 15 permanecem como `- etl`.

### Prompt 21 - site institucional

**Enviado:**
> não crie os arquivos da conexão com o bucket
> mas me mostre como ficaria

**Resultado da IA:**
- Nenhum arquivo foi criado; foram mostrados apenas os esqueletos de `src/routes/dashboardDados.js`, `app.js`, `src/controllers/dashboardDadosController.js`, `src/models/s3GoldModel.js`, o `fetch` no front e a inclusão de `AWS_REGION` no `.env.example`.
- Mantidos o isolamento por `req.usuario.empresa`, a validação de vínculo do servidor, o `204` para "sem dados" e o cabeçalho CSV do agente; observado que o Gold guarda CSV, apesar de o `AGENTS.md` citar JSON.

### Prompt 22 - site institucional

**Enviado:**
> sempre que tiver mais prompts, salve um pequeno relatório no arquivo diario_sarah.md do repositório etl

**Resultado da IA:**
- Confirmado o registro contínuo de resumos curtos em `etl/diario_sarah.md`, inclusive para prompts enviados no repositório do site.
- Regra reforçada no `AGENTS.md` do ETL e adicionada ao `AGENTS.md` do site para valer em sessões futuras; este prompt foi registrado como Prompt 22.

### Prompt 23 - site institucional

**Enviado:**
> e o prompt sobre não criar os arquivos, mas mostrar como ficaria?

**Resultado da IA:**
- Verificado que o prompt estava faltando no diário; inserido como Prompt 21 e o prompt seguinte renumerado de 21 para 22.
- Registrado o esqueleto das rotas/controller/model S3 e do `fetch` como apenas demonstrativo, sem criação de arquivos no repositório do site.



## 2026-11-24

### Prompt 1 - site institucional

**Enviado:**
> as rotas poderiam estar nas rotas do banco de dados?
> por exemplo: dashboard_sarahController.js?

**Resultado da IA:**
- Confirmado que dá para reaproveitar `src/routes/dashboardSarah.js` e `src/controllers/dashboardSarahController.js`, adicionando endpoints que chamam um model S3 separado, sem criar um novo controller.
- Avisado que o arquivo se chama `dashboardSarahController.js` (camelCase) e que o mount `/dashboardSarah` em `app.js:91` não tem `autenticar`, embora o controller já use `req.usuario.empresa`.
- Observado que `public/html/dashboard_sarah.html:358` já consome `/dashboardSarah/buscarServidoresPorEmpresa/...`, então manter o prefixo evita quebrar a tela.

### Prompt 2 - site institucional

**Enviado:**
> adicione as rotas feitas lá em cima nas rotas já existentes, incluindo o app.js

**Resultado da IA:**
- Rotas `/servidores`, `/historico/:idServidor` e `/resumo` adicionadas em `src/routes/dashboardSarah.js`, com as funções correspondentes em `src/controllers/dashboardSarahController.js`.
- Criado `src/models/s3GoldModel.js` para listar/ler os CSV do Gold (`ListObjectsV2Command`/`GetObjectCommand`), mantendo empresa e vínculo validados pela sessão e intervalo máximo de 31 dias.
- `app.js` passou a aplicar `autenticar` no mount `/dashboardSarah`, e `.env.example` ganhou `AWS_REGION`.

### Prompt 3 - site institucional

**Enviado:**
> tem partes do código que recebe um csv do gold, o gold armazena json
> altere os scripts para que receba json

**Resultado da IA:**
- `src/models/s3GoldModel.js` reescrito para ler `dashboard_sarah.json` com `JSON.parse(await objeto.Body.transformToString())`, removendo o parser de CSV e o cabeçalho antigo.
- Prefixo ajustado para o caminho real gerado pelo ETL (`03-gold/empresa_id=<id>/servidor_id=<id>/data=<data>/dashboard_sarah.json`), sem o trecho `metricas/`; `lerServidor` devolve as `capturas` e `ultimaLeitura` devolve a última captura com `medias`, `lote` e `nomeServidor`.
- Controller e rotas não precisaram mudar; sintaxe validada com `node --check`.

### Prompt 4 - site institucional

**Enviado:**
> toda vez que mudar o dia os prompts se reiniciam e coloca um subtitulo da data
> igual está no diário

**Resultado da IA:**
- Criada a seção `## 2026-11-24` com numeração reiniciada em `Prompt 1`; as entradas anteriores permanecem em `2026-11-23`.
- Regra reforçada no `AGENTS.md` do ETL e do site para criar nova seção por data e reiniciar a contagem a cada dia.

### Prompt 5 - site institucional

**Enviado:**
> no bucket eu adicionei o arquivo perfeito-sarah.json no bucket, sem nenhum prefixo e pastas
> apenas o arquivo direto no bucket a fins de testes
> quero testar buscando com um arquivo básico
> para ver se chega no s3 mesmo
> faça rotas momentâneas somente para testar se a conexão com o s3 funciona

**Resultado da IA:**
- Rotas temporárias `/dashboardSarah/teste-s3` e `/dashboardSarah/teste-s3-lista` adicionadas em `src/routes/dashboardSarah.js`, protegidas pela sessão e por `monitorar`.
- `s3GoldModel.js` ganhou `lerChave('perfeito-sarah.json')` e `listarRaiz(10)`; o controller devolve o JSON lido, bucket/região e a lista de objetos da raiz, ou o erro do SDK em `500`.
- Sintaxe validada com `node --check`; as rotas são apenas para diagnóstico e podem ser removidas depois do teste.

### Prompt 6 - site institucional

**Enviado:**
> como testar se está funcionando?

**Resultado da IA:**
- Orientado a subir o servidor com `npm start`, entrar com um usuário válido para gerar o cookie de sessão e chamar `/dashboardSarah/teste-s3` (e `/teste-s3-lista`) pelo próprio navegador, sem configuração extra de `fetch`.
- Respostas esperadas descritas com `ok: true`, bucket/região e o JSON lido; interpretação de `401`, `403` e `500` conforme sessão/permissão/credenciais S3.

### Prompt 7 - site institucional

**Enviado:**
> deu algum erro no fetch

**Resultado da IA:**
- Pedido para rodar `fetch('/dashboardSarah/teste-s3').then(async r => console.log(r.status, await r.text()))` e informar status/corpo, além de checar o console do servidor.
- Causas prováveis listadas: sem sessão (`401`), cargo sem `monitorar` (`403`) e credenciais AWS ausentes na máquina local ou bucket/região divergentes (`500`).

### Prompt 8 - site institucional

**Enviado:**
> {"ok":false,"bucket":"bucket-hmg-infratech-sarah","erro":"Could not load credentials from any providers"}
> eu modifiquei o iam para labinstanceprofile

**Resultado da IA:**
- Explicado que IAM Instance Profile só fornece credenciais automáticas dentro da EC2; no computador local o SDK continua sem credenciais.
- Orientado a preencher `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` e, se forem credenciais temporárias de laboratório, `AWS_SESSION_TOKEN` no `.env.dev` local (ou via `~/.aws/credentials`), sem compartilhar as chaves no chat; na EC2, basta reiniciar a app após anexar o perfil.

### Prompt 9 - site institucional

**Enviado:**
> reiniciei a aws

**Resultado da IA:**
- Orientado a garantir que a aplicação Node voltou a rodar depois do reboot da EC2 e a repetir o `fetch` na mesma aba logada.
- Se ainda aparecer `Could not load credentials`, verificar o perfil anexado pelo IMDS (`curl http://169.254.169.254/latest/meta-data/iam/security-credentials/`) e confirmar se o teste não está sendo feito no PC local, onde o instance profile não fornece credenciais.

### Prompt 10 - site institucional

**Enviado:**
> deu os seguintes erros:
> "Failed to load resource: net::ERR_CONNECTION_REFUSED"
> "buscar dados: TypeError: Faild to fetch

**Resultado da IA:**
- Explicado que `ERR_CONNECTION_REFUSED` significa que não há servidor ouvindo na porta usada pelo `fetch`; o Node precisa ser iniciado novamente após o reboot da EC2 e a página deve ser aberta pelo endereço do app (porta `3333` do `.env.dev`), não por arquivo local ou outra porta.
- Orientado a validar no servidor com `curl -I http://localhost:3333/` e conferir os logs de inicialização (`ARQUIVO ENV`, `BANCO CARREGADO`), além de verificar se `localhost` no navegador não aponta para o PC em vez da EC2.
