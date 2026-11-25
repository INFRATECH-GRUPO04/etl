# InfraTech — Contexto do Projeto

Mesmo arquivo nos 4 repositórios (site, logs/Java, ETL, script de captura). Leia as seções 1–7 (negócio), a nota do seu repositório na seção 8 e o acordo de fluxo na seção 9. Quando houver conflito, **o código atual vale mais que PDFs antigos e que este documento**. Se algo não estiver aqui ou estiver incerto, pergunte antes de assumir.

## 1. Contexto

Projeto acadêmico (SPTech, 2026.2) de monitoramento de infraestrutura de **servidores físicos de jogos online**. Equipes de TI dos clientes perdem tempo achando falhas porque não têm visão centralizada de CPU, RAM, disco e rede. O InfraTech coleta essas métricas, processa em um data lake e mostra dashboards e alertas, no estilo NOC/ITIL.

Personas que guiam o desenvolvimento (a equipe InfraTech **não** é persona):
- **Gestor**: gestor de infraestrutura ou CEO do cliente. Quer visão geral da frota e decide com base nela.
- **Analista de telemetria (dados)**: investiga um servidor, tendências e limites. Perfil `ANALISTA_DADOS`.
- **Analista NOC (DevOps)**: acompanha saúde e capacidade e reage a alertas. Perfil `ANALISTA_NOC`.

## 2. Objetivo

Reduzir o tempo para detectar e diagnosticar falhas, com dashboards por persona, alertas automáticos quando métricas passam dos limites e histórico para análise.

## 3. Escopo

**Dentro (esta versão):**
- Site institucional público e login por perfil.
- Cadastro/gestão de empresas, funcionários, tokens de convite e servidores com componentes e limites.
- Coleta (CPU, RAM, disco, rede, processos), ETL em camadas e dashboards (geral, específica, saúde e capacidade).
- Alertas por e-mail/chat da equipe InfraTech via Slack e Jira.

**Fora do escopo** (não implementar nem sugerir sem pedido):
- Monitoramento de microtransações (apesar do título do projeto).
- Instâncias virtuais, containers ou nuvem do cliente; só servidores físicos.
- Correção automática de falhas, app mobile, formulário de contato dentro do sistema.
- Slack/Jira por empresa cliente (são ferramentas internas da InfraTech).
- Planos, limites de servidores/funcionários por empresa, expiração de token por data.
- Hardening de segurança (hash de senha, etc.), exceto onde afeta a funcionalidade em construção.
- Tempo real abaixo de 1 min, previsão por ML, exportação de relatórios, SSO, recuperação de senha por e-mail.

## 4. Funcionalidades

- **Equipe interna (`ADMIN`):** cadastra e gerencia empresas clientes (edita, desativa, reativa). Não vê dashboards de clientes.
- **Gestor:** vê dashboards e servidores da própria empresa, cadastra servidores, gera tokens, gerencia funcionários (dados, desativar, vínculo a servidores) e pode liberar a edição de limites para analistas de dados.
- **Analistas e funcionários:** só visualizam dashboards da própria empresa, conforme o cargo (ver `public/js/permissoes.js`). Analista de dados só edita limites de alerta se o gestor liberar (`pode_editar_alertas`).
- **Coleta:** o script local envia lotes de CSV; o ETL promove os dados de bronze para silver e gold.
- **Alertas:** compara leituras com limites de atenção/crítico por componente e notifica.

## 5. Regras de negócio

- Dados sempre isolados por empresa: toda consulta de funcionário/servidor filtra por `fk_empresa` da sessão. Nunca confie em ID vindo do front.
- Exclusão é **lógica** (`status_sistema = 0`). Empresas, funcionários e servidores desativados somem das telas ativas e podem ser reativados. Nada é apagado de empresa/funcionário.
- A empresa interna (InfraTech, `id_empresa = 1`) fica oculta e protegida contra edição/desativação.
- Cadastro de empresa: todos os campos obrigatórios, exceto complemento. Cria o login do gestor com e-mail e senha iniciais; nome, CPF e nascimento o gestor completa depois. Um gestor por empresa.
- Cadastro de funcionário só por **token** gerado pelo gestor, controlado por `quantidade_uso`/`quantidade_usada`, sem validade por data.
- Visibilidade de servidores: funcionário sem vínculo em `servidor_funcionario` vê todos da empresa; com vínculo, só os vinculados. Gestor sempre vê todos.
- Limites: cada componente tem limite de atenção e crítico (atenção < crítico, 0–100%). Existe padrão global por empresa e ajuste por servidor.
- E-mail de login deve ser único; CNPJ de empresa também.
- Toda ação de cadastrar, editar, desativar ou reativar pede confirmação antes de executar.
- Cargos válidos: `INFRATECH`, `GESTOR`, `ANALISTA_DADOS`, `ANALISTA_NOC`, `ADMIN`, `FUNCIONARIO` (maiúsculo, sem acento). `ADMIN` e `INFRATECH` deveriam ser equivalentes; não mexer nisso.
- Ainda sem decisão (não implementar por conta própria): bloquear login de empresa desativada; aceitar ou não token de empresa desativada.

## 6. Interface

- Tema escuro, cor de destaque ciano `#00CFFF`; fontes Inter e JetBrains Mono. Reaproveite cores e componentes existentes, sem criar paleta nova.
- Cada tela tem CSS próprio; evite editar CSS compartilhado (`global.css`). Hoje não há variáveis CSS (hex direto). Isso será revisado depois: não refatore sem pedir.
- Sidebar compartilhada, filtrada por cargo. Layout responsivo (gaveta no mobile).
- Mensagens de erro/sucesso claras e confirmação em ações destrutivas.
- Dashboards hoje usam dados **mock** (`public/js/dashboard-data.js`). Meta: o back-end expor rotas com dados da camada gold e o front montar os gráficos a partir delas.

## 7. Critérios de aceitação (por fluxo)

- **Login:** credenciais válidas levam à página do cargo; inválidas mostram erro; sem sessão, páginas protegidas redirecionam ao login.
- **Empresas:** só `ADMIN` acessa; cadastro exige campos obrigatórios; desativar não apaga dados; a empresa interna não aparece.
- **Funcionários:** token só vale enquanto houver usos; gestor só vê e altera funcionários da própria empresa.
- **Servidores:** cadastro com componentes e limites válidos; chave do agente gerada sob demanda; usuário não acessa servidor de outra empresa.
- **Coleta e ETL:** CSV com cabeçalho exato chega ao prefixo `01-bronze/`; Lambdas geram `02-silver/` e `03-gold/` sem perder linhas.
- **Dashboards:** cores e contagens seguem os limites configurados; sem dados mostra "sem dados", nunca zero falso.
- **Alertas:** leitura acima do limite gera notificação Slack/Jira uma vez por ocorrência.

## 8. Arquitetura e notas por repositório

**Fluxo:** servidor do cliente (script Python local) → CSV → `POST /agente/enviar` no back-end → S3 (Data Lake) `01-bronze/` → Lambda bronze→silver → `02-silver/` → (a) Lambda silver→gold → `03-gold/` para os dashboards do site; (b) aplicação Java lê os **CSVs da `02-silver/` diretamente**, cruza com os limites no MySQL e notifica Slack/Jira. **Não existe `latest.json` em nenhum prefixo.** Back-end Node/Express expõe rotas com dados do gold para as dashboards (HTML/CSS/JS + Chart.js). Node, Java e MySQL rodam em containers Docker numa **única EC2**.

**Captura:** acordo: coleta a cada 1 min; ao acumular 10 capturas (10 min) envia o lote. O código pode estar com outros valores (`5s`/`5` capturas) — confirme em `captura_servidor.py` antes de assumir. Autentica com `x-chave-servidor`. Cabeçalhos do CSV (`dados`, `processos`) são definidos em `CABECALHOS` no `agenteController.js` e devem ser idênticos nos dois lados; o Java também consome esse cabeçalho (3º consumidor).

**Site:**
- `app.js` fixa `ambiente_processo = 'desenvolvimento'` e carrega `.env.dev` (a partir de `.env.example`); sem isso a porta e o banco não carregam. `npm install`, `npm start` ou `npm run dev`. Não há testes/lint/CI: valide manualmente.
- Fluxo `routes → controllers → models → database/config.js`; sempre use consultas parametrizadas. SQL em `snake_case`, JS em `camelCase` via alias.
- Front é plano (`public/html|css|js`) mas as URLs são aninhadas; o middleware de `app.js` resolve pelo nome do arquivo. Não mova nem duplique nomes.
- `public/js/permissoes.js` é a única fonte das regras de acesso (usada também no back-end).
- Sessão: cookie HMAC `infratech_session` (12 h). Sem `AUTH_TOKEN_SECRET`, restart desloga todos.
- Schema: fonte da verdade é `src/database/infratech-atualizado.sql`; não altere schema sem alinhar. Atenção: o código usa a tabela `limite_alerta_global`, que só existe em `SCRIPT-INFRATECH.sql`.
- O back-end lê `03-gold/empresa-{id}/dash-geral.json`; a seção 9 descreve quem grava esse arquivo.

**ETL:** duas Lambdas Python (bronze→silver, silver→gold), prefixos `01-bronze`, `02-silver`, `03-gold`. A bronze→silver fica como está: sem `latest.json`, sem watermark e sem mudança de formato. A silver→gold roda a cada 30 min, usa janela de 40 dias e grava `03-gold/empresa-{id}/dash-geral.json` (nome que o site lê; não `dashboard.json`).

**Logs/Java:** `mvn -q package` gera o jar executável; `Dockerfile` + `.env.example` para rodar local/EC2. O Java lê `02-silver/metricas/` com `S3_SILVER_PREFIX` (não usa mais `S3_ALERTA_PREFIX`) e precisa de `s3:ListBucket` na silver além de `GetObject`. Config fora do repo em `~/.config/infratech/alertas.env`. Não há testes/lint/CI: validar manualmente.

**Script de captura:** acordo: `INTERVALO = 60`, `CAPTURAS_POR_LOTE = 10` (se o arquivo ainda estiver em `5`/`5`, ajustar só quando pedido). `bytes_enviados`/`bytes_recebidos` são contadores cumulativos, não Mbps. O `lote` no nome do arquivo é `YYYYMMDDTHHMMSSZ`, mas é gerado em `America/Sao_Paulo`: o `Z` é enganoso.

## 9. Acordo de fluxo, cadências e alertas (25/11/2026)

Vale para os 4 repositórios. Se o código divergir, **o código atual vence**; alinhe antes de mudar.

**Coleta (script_python):** 1 captura por minuto, lote de 10 capturas enviado a cada 10 min via `POST /agente/enviar`. Cabeçalho fixo e IDs de empresa/servidor no nome do arquivo e dentro do CSV. `bytes_enviados`/`bytes_recebidos` são cumulativos — a conversão para Mbps é feita depois, pelo Java.

**Bronze → silver (ETL):** inalterado. Chaves `02-silver/metricas/empresa_id={id}/servidor_id={id}/data=AAAA-MM-DD/<lote>_empresa-{id}_servidor-{id}_dados.csv`. Um lote que cruza meia-noite gera dois arquivos com o mesmo `lote`. **Não gerar `latest.json` em lugar nenhum.**

**Silver → gold (ETL):** execução a cada 30 min; janela máxima de 40 dias; filtrar a chave `data=` antes de baixar/parsear para não reprocessar tudo; gravar `03-gold/empresa-{id}/dash-geral.json`.

**Java de alertas (logs):** lê os CSVs da silver diretamente. Usa `S3_SILVER_PREFIX` (default `02-silver/metricas/`); listagem paginada; filtra `_dados.csv` das partições de hoje/ontem; monta as amostras com parser manual (`split(",", -1)` + índice do cabeçalho, sem dependência nova). Watermark **por `(empresa, servidor)`**, usando o `lote` do nome do arquivo (interpretado como Brasília, não UTC); sem lote novo, não faz GET. Agrupa os dois arquivos de um mesmo lote quando ele cruza meia-noite. Para a rede, lê também o lote anterior do servidor e calcula `Mbps = (bytes[t] - bytes[t-1]) / Δt * 8 / 1e6`, com `Δt` real dos `data_hora` e delta negativo tratado como 0.

**Regra de alerta:** janela de **10 min** contada a partir do maior `data_hora` lido (equivalente ao antigo `gerado_em`); a **média** das amostras define `CRÍTICO`/`ATENÇÃO`; gera chamado quando **7 de 10** amostras estão acima do limite escolhido, **sem exigir que sejam consecutivas**; se não chegar dado novo por mais de **15 min**, loga aviso e não cria chamado — essa checagem vem **antes** do `return` do watermark. Configurável por env: `ALERTA_JANELA_MINUTOS=10`, `ALERTA_MIN_AMOSTRAS=7`, `ALERTA_MAX_IDADE_MINUTOS=15`. O loop do Java continua usando `ALERTA_INTERVALO_SEGUNDOS`. A descrição do Jira inclui a leitura mais recente e a janela avaliada. Slack/Jira são ferramentas internas da InfraTech, um chamado por ocorrência.

**Site:** segue lendo `03-gold/empresa-{id}/dash-geral.json`; nada muda no front por causa deste acordo.
