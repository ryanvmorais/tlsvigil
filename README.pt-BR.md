# TLSVigil

[English](README.md) · **Português (Brasil)**

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![runtime deps](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
[![CI](https://img.shields.io/github/actions/workflow/status/ryanvmorais/tlsvigil/ci.yml?label=CI&logo=githubactions&logoColor=white)](https://github.com/ryanvmorais/tlsvigil/actions/workflows/ci.yml)
![license](https://img.shields.io/badge/license-Apache%202.0-blue?logo=apache&logoColor=white)

**O TLSVigil é um monitor de vencimento de certificado TLS e de domínio, sem dependências: uma
ferramenta de linha de comando e uma GitHub Action que avisam, antes que aconteça, que o
certificado do seu site ou o registro do seu domínio está para vencer.**

Um certificado vencido derruba o site com um alerta do navegador que espanta o visitante, e um
domínio vencido derruba o site e o e-mail juntos. O Let's Encrypt parou de mandar e-mails de aviso
de vencimento em junho de 2025, e os certificados ficam mais curtos a cada ano, então a renovação
que falha em silêncio virou o caso comum. O TLSVigil confere cada host e cada domínio uma vez por
dia e avisa enquanto ainda dá tempo de agir.

Ele foi feito para pequenas empresas, donos de site e freelancers que cuidam de alguns sites, e não
precisa de servidor: roda num agendamento diário num repositório seu no GitHub, nos minutos
gratuitos do GitHub Actions, e abre uma issue lá quando algo pede atenção.

> **Status:** em desenvolvimento, ainda não lançado; o primeiro lançamento será a `1.0.0`. Este
> README descreve a ferramenta como ela está especificada em [specs/](specs/). Hoje o
> `tlsvigil check` roda a partir de um clone, mostra o resultado e, com `--notify`, entrega o
> resultado na issue do GitHub, no Telegram ou por e-mail; a Action roda o comando num workflow, e
> o repositório-modelo ainda não foi publicado (os arquivos dele estão em [template/](template/)).

## O que ele verifica

O TLSVigil responde uma pergunta para cada nome que você dá: **quantos dias faltam, e isso é
suficiente?** Um host ou um domínio entra no aviso quando faltam menos dias que o seu limite de
aviso, e um certificado que falha na verificação entra na hora, seja qual for a data.

| Verificação | O que diz | Lê | Rede |
|---|---|---|---|
| Certificado | Quantos dias faltam para o certificado de `host:porta` vencer, e se ele falha na verificação (vencido, nome errado, cadeia não confiável) | O certificado que o servidor apresenta no handshake TLS | Uma conexão TLS com cada host da lista |
| Domínio | Quantos dias faltam para o registro do domínio vencer | O evento `expiration` do registro RDAP do domínio | HTTPS para o servidor RDAP que o bootstrap da IANA indica para o domínio de topo |

O resultado vai para uma issue só no seu repositório, pelo `GITHUB_TOKEN` que o workflow já tem: a
issue é aberta quando algo pede atenção, atualizada nas execuções seguintes e fechada quando tudo
volta ao normal, então um problema que dura uma semana é uma issue, não sete. Telegram e e-mail
(SMTP) são canais extras opcionais. Na linha de comando, o mesmo resultado sai em texto ou em JSON
(`--json`), em inglês ou em português do Brasil (`--lang pt-BR`), com um código de saída que um
script pode testar.

### Em que você pode confiar

- **Passivo.** Ele abre uma conexão TLS como um navegador faz e consulta um serviço público de
  registro. Não envia carga de ataque e não faz login em lugar nenhum.
- **Zero dependências em tempo de execução.** O pacote usa só a biblioteca padrão do Python, então
  o que roda no seu CI é o interpretador e este código.
- **Só TLS verificado.** A data do certificado é lida só de uma cadeia que passou na verificação;
  uma cadeia que falha é o próprio aviso, nunca uma aprovação silenciosa.
- **Requisições com limite.** Cada consulta RDAP é HTTPS, para um servidor que o bootstrap da IANA
  indica, com limite de tempo e de tamanho da resposta.
- **Seus segredos continuam segredos.** Tokens e senhas vêm do cofre de segredos do CI e nunca
  chegam a uma saída, a uma linha de log ou a uma issue.

## Início rápido

Ainda não publicado, então por enquanto rode a partir de um clone. Você precisa do
[uv](https://docs.astral.sh/uv/).

```bash
# Clone o repositório e instale o ambiente
git clone https://github.com/ryanvmorais/tlsvigil.git
cd tlsvigil
uv sync

# Confira um host e um domínio, sem arquivo de configuração
uv run tlsvigil check --host example.com --domain example.com --lang pt-BR
```

Cada nome ganha uma linha, e a última linha resume a execução:

```text
OK            example.com:443  certificado vence em 86 dias (2027-02-22)
OK            example.com      domínio vence em 258 dias (2027-08-13)

2 verificados: 0 precisam de atenção.
```

Para acompanhar a sua própria lista, escreva-a num arquivo `tlsvigil.toml` (próxima seção) e
rode `uv run tlsvigil check` na mesma pasta.

## Configuração

O `tlsvigil check` lê o `tlsvigil.toml` da pasta atual, ou o arquivo indicado por
`--config CAMINHO`. Um exemplo comentado:

```toml
# Opcional: "en" (padrão) ou "pt-BR".
lang = "pt-BR"
# Opcional: segundos para cada conexão TLS e cada consulta RDAP (padrão 10).
timeout = 10

[certificates]
# Avisa quando faltarem menos dias do que este (padrão 14).
warn_days = 14
hosts = [
  "example.com",                                  # porta 443
  "mail.example.com:465",
  "[2001:db8::1]:8443",
  { host = "legacy.example.com", warn_days = 30 },
]

[domains]
# Avisa quando faltarem menos dias do que este (padrão 30).
warn_days = 30
names = ["example.com", "example.com.br"]

[notifications]
# Os canais que o --notify avisa, nesta ordem: "github", "telegram", "smtp".
channels = ["github"]

# Lida só quando "smtp" está em channels; o usuário, a senha e os endereços
# vêm de variáveis de ambiente, nunca deste arquivo.
[notifications.smtp]
host = "smtp.example.com"
port = 587                                        # padrão: 587 para "starttls", 465 para "tls"
security = "starttls"
```

| Chave | Padrão | O que guarda |
|---|---|---|
| `lang` | `"en"` | O idioma da saída em texto, das mensagens de erro e das notificações: `"en"` ou `"pt-BR"` |
| `timeout` | `10` | Os segundos que cada conexão TLS e cada consulta RDAP podem levar, maior que `0` |
| `certificates.warn_days` | `14` | Avisa quando faltarem menos dias do que este para o certificado vencer (`0` ou mais) |
| `certificates.hosts` | nenhum | Os hosts a conferir: `"host"` (porta 443), `"host:porta"`, `"[IPv6]:porta"`, ou uma tabela `{ host, port, warn_days }` |
| `domains.warn_days` | `30` | Avisa quando faltarem menos dias do que este para o registro do domínio vencer (`0` ou mais) |
| `domains.names` | nenhum | Os domínios a conferir, ou uma tabela `{ name, warn_days }`. Escreva cada domínio como foi registrado (`example.com`, não `www.example.com`), e um nome internacionalizado na forma `xn--` |
| `notifications.channels` | nenhum | Os canais que o `--notify` avisa, cada um uma vez: `"github"`, `"telegram"`, `"smtp"` |
| `notifications.smtp.host` | nenhum | O servidor SMTP, obrigatório quando `"smtp"` está na lista |
| `notifications.smtp.port` | `587` ou `465` | A porta do servidor, de `1` a `65535`; o padrão segue o `security` |
| `notifications.smtp.security` | `"starttls"` | `"starttls"` (uma conexão simples que passa a TLS antes do login) ou `"tls"` (TLS desde o primeiro byte); não existe modo em texto puro |

O `warn_days` de uma entrada vale mais que o da tabela dela. O arquivo inteiro é conferido antes
de qualquer conexão: uma chave desconhecida (um erro de digitação como `warn_day`), um valor do
tipo errado, um host ou um domínio repetido, ou um arquivo sem nada para conferir interrompe a
execução, e cada problema aparece com o lugar onde está (como `certificates.hosts[2]`). Um
token, uma senha, um chat ou um endereço escrito no arquivo (`password`, `to`, `chat_id`...)
também é recusado, com o nome da variável de ambiente onde ele deve ficar.

## Linha de comando

```text
tlsvigil check [--config CAMINHO | --host HOST[:PORTA]... --domain NOME...] [--json] [--lang en|pt-BR]
               [--notify [--state CAMINHO]]
```

| Opção | O que faz |
|---|---|
| `--config CAMINHO` | Lê este arquivo em vez do `tlsvigil.toml` |
| `--host HOST[:PORTA]` | Confere o certificado deste host em vez de ler um arquivo; repita para mais hosts |
| `--domain NOME` | Confere o registro deste domínio em vez de ler um arquivo; repita para mais domínios |
| `--json` | Escreve um documento JSON (versão de esquema `1`) com cada resultado e uma contagem por status, com as mesmas chaves e valores em inglês em qualquer idioma; com `--notify`, uma lista `notifications` com o resultado de cada canal |
| `--lang en\|pt-BR` | O idioma da saída em texto e das notificações; vale mais que o `lang` do arquivo |
| `--notify` | Entrega o resultado nos canais de `[notifications]` (próxima seção); só com um arquivo, não com `--host` nem `--domain` |
| `--state CAMINHO` | O arquivo onde o `--notify` guarda o que cada canal já ouviu (padrão `.tlsvigil-state.json` na pasta atual); só com `--notify` |

O código de saída diz a um script o que aconteceu:

| Código | Significado |
|---|---|
| `0` | Todo resultado é `ok`, ou `unknown` (um domínio sem serviço RDAP ou sem data) |
| `1` | Pelo menos um resultado pede atenção: `expiring`, `invalid` (um certificado que falha na verificação) ou `error` (a verificação não terminou) |
| `2` | Nada foi conferido: erro de uso, erro de configuração, ou nada para conferir |
| `3` | Um erro interno (um bug); o traceback sai na saída de erro |
| `4` | Com `--notify`, uma notificação não pôde ser entregue, seja qual for o resultado das verificações; a linha da entrega diz por quê |

## Notificações

Com `--notify`, o `tlsvigil check` faz as verificações como sempre e depois entrega o resultado em
cada canal de `[notifications]`, na ordem da lista:

- **`github`** mantém uma issue só no repositório da execução: abre a issue quando algo pede
  atenção, reescreve o título e o corpo dela a cada execução com os avisos atuais, acrescenta um
  comentário quando há novidade e fecha a issue quando nenhum aviso sobra. Ele só mexe na issue
  que ele abriu, e uma issue que você fecha continua fechada até haver novidade, que abre uma nova.
- **`telegram`** manda uma mensagem em texto puro para um chat, por um bot seu.
- **`smtp`** manda um e-mail em texto puro para um ou mais destinatários, por TLS, com login no
  servidor de `[notifications.smtp]`; um servidor que não oferece STARTTLS, ou cujo certificado
  falha na verificação, nunca recebe o usuário nem a senha.

O relatório sai como antes, com uma linha por canal logo depois do resumo:

```text
6 verificados: 1 precisa de atenção.
github: enviado (issue #12)
telegram: nada de novo
smtp: falhou: o servidor SMTP recusou 1 destinatário
```

### Segredos e endereços

Os tokens, a senha, o chat e os endereços de e-mail vêm de variáveis de ambiente, nunca do arquivo,
e só as variáveis dos canais da lista são lidas. Uma variável ausente ou malformada interrompe a
execução antes de qualquer verificação (código de saída `2`), com o nome da variável e nunca o
valor dela, e nenhum desses valores chega à saída, ao JSON, ao arquivo de estado ou a uma mensagem.

| Variável | Canal | O que guarda |
|---|---|---|
| `GITHUB_TOKEN` | `github` | Um token que pode escrever issues; no GitHub Actions, `${{ github.token }}` |
| `GITHUB_REPOSITORY` | `github` | O repositório da issue, como `dono/nome`; o GitHub Actions preenche |
| `TLSVIGIL_TELEGRAM_TOKEN` | `telegram` | O token do seu bot, como o BotFather entrega (`123456:ABC-...`) |
| `TLSVIGIL_TELEGRAM_CHAT_ID` | `telegram` | O chat: o número dele (negativo para um grupo) ou `@` e o nome de um canal público |
| `TLSVIGIL_SMTP_USERNAME` | `smtp` | O usuário que faz login no servidor |
| `TLSVIGIL_SMTP_PASSWORD` | `smtp` | A senha dele |
| `TLSVIGIL_SMTP_FROM` | `smtp` | O remetente, um endereço sem nome de exibição (`alertas@example.com`) |
| `TLSVIGIL_SMTP_TO` | `smtp` | Um ou mais destinatários, separados por vírgula |

Num workflow, o canal da issue precisa da permissão de escrever issues e do token no ambiente do
passo; os outros valores vêm dos segredos do repositório:

```yaml
permissions:
  contents: read
  issues: write

# No passo que roda tlsvigil check --notify:
env:
  GITHUB_TOKEN: ${{ github.token }}
  TLSVIGIL_TELEGRAM_TOKEN: ${{ secrets.TLSVIGIL_TELEGRAM_TOKEN }}
  TLSVIGIL_TELEGRAM_CHAT_ID: ${{ secrets.TLSVIGIL_TELEGRAM_CHAT_ID }}
```

### Quando um canal fica sabendo de um aviso

Um resultado é um aviso quando é `expiring` ou `invalid`, ou quando é `error` em duas execuções
seguidas, porque uma conexão que falha uma vez costuma ser um tropeço passageiro; `ok` e `unknown`
nunca são. Cada canal recebe no máximo uma mensagem por execução, no idioma da execução, e só
quando há novidade:

- **Um aviso novo**, ou **uma mudança** de status ou de motivo (um certificado que ia vencer e
  venceu, um host que passou de `error` para `invalid`).
- **Um lembrete** quando os dias que faltam chegam a 14, 7, 3, 1 ou 0, contando só os dias abaixo
  do `warn_days` da entrada, e mais um quando um domínio passa da data. Depois de execuções
  atrasadas ou puladas, um lembrete só, para o menor dia alcançado, e nenhum para os dias pulados.
- **O fim de um aviso:** resolvido quando a entrada volta a `ok`, ou encerrado sem resolver quando
  ela não pode mais ser conferida (`unknown`) ou saiu da configuração.

O que cada canal já ouviu fica no arquivo de estado (`--state`). Guarde esse arquivo entre as
execuções: sem ele, toda execução é a primeira e cada aviso é dado de novo, nunca escondido. Um
canal cuja entrega falha guarda o que tinha ouvido antes, então a execução seguinte tenta de novo,
e os canais que deram certo não ouvem duas vezes.

## GitHub Actions

A Action roda o `tlsvigil check --notify` num runner hospedado pelo GitHub, com o Python do próprio
runner e nada para instalar, e guarda o arquivo de estado entre as execuções por você. Só com o
canal `github` ela não precisa de segredo nenhum: a issue é escrita com o token do próprio workflow.

### Comece pelo modelo

O repositório-modelo traz um `tlsvigil.toml`, o workflow diário e um arquivo do Dependabot que
propõe cada release nova do TLSVigil. Ele ainda não foi publicado; os arquivos dele estão em
[template/](template/).

1. **Crie o seu repositório** a partir do modelo (**Use this template** → **Create a new
   repository**), público ou privado. Não como fork: um fork começa com o agendamento desligado.
2. **Diga o que verificar:** edite o `tlsvigil.toml` e faça o commit na `main`. Esse commit dispara
   uma verificação; a aba **Actions** mostra a execução, com o resultado no resumo dela.
3. **Acompanhe o repositório** com **Watch** → **All Activity**. A issue é aberta por
   `github-actions[bot]`, e o GitHub só notifica quem acompanha o repositório; num repositório
   movimentado, **Custom** → **Issues** é mais silencioso.
4. **Opcional:** acrescente `telegram` ou `smtp` em `channels`, os segredos do canal no
   repositório, e tire o comentário das linhas dele no workflow.

### Use num repositório seu

Ponha o `tlsvigil.toml` na raiz do repositório e este workflow em
`.github/workflows/tlsvigil.yml`; ele é o do modelo, palavra por palavra:

```yaml
# TLSVigil: checks the certificates and domains of tlsvigil.toml every day and keeps one issue of
# this repository up to date with what needs attention. Run by GitHub Actions on the schedule, by
# hand (Actions → TLSVigil → Run workflow) and on a push to main that changes tlsvigil.toml.
# The run is red when nothing could be checked or a notification was not delivered; a warning,
# a host that could not be reached among them, is an issue, not a red run.
name: TLSVigil

on:
  schedule:
    - cron: "17 7 * * *" # every day at 07:17 UTC; GitHub may start it late
  workflow_dispatch:
  push:
    branches: [main]
    paths: [tlsvigil.toml]

# contents: read to check out tlsvigil.toml; issues: write for the "github" channel only.
permissions:
  contents: read
  issues: write

# One check at a time: a second run waits for the first, so the saved state is never raced.
concurrency:
  group: tlsvigil
  cancel-in-progress: false

jobs:
  check:
    name: Check
    runs-on: ubuntu-26.04
    timeout-minutes: 10
    steps:
      - name: Check out the configuration
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - name: Run TLSVigil
        uses: ryanvmorais/tlsvigil@3a6171c98cf91597b3746f105730d23f5abea5b0 # main
        # Telegram or e-mail: add the secrets (Settings → Secrets and variables → Actions), add the
        # channel to tlsvigil.toml and uncomment its lines.
        # with:
        #   telegram-token: ${{ secrets.TLSVIGIL_TELEGRAM_TOKEN }}
        #   telegram-chat-id: ${{ secrets.TLSVIGIL_TELEGRAM_CHAT_ID }}
        #   smtp-username: ${{ secrets.TLSVIGIL_SMTP_USERNAME }}
        #   smtp-password: ${{ secrets.TLSVIGIL_SMTP_PASSWORD }}
        #   smtp-from: ${{ secrets.TLSVIGIL_SMTP_FROM }}
        #   smtp-to: ${{ secrets.TLSVIGIL_SMTP_TO }}
```

Fixe a Action num SHA de commit completo, como acima, nunca numa branch ou numa tag que pode
mudar. As duas permissões são tudo de que ela precisa: `contents: read` para o checkout e
`issues: write` para a issue; o cache que guarda o estado não pede nenhuma. Mantenha o grupo de
concorrência num workflow seu, para que uma execução disparada à mão espere a agendada em vez de
disputar o estado com ela.

### Entradas e saída

| Entrada | Padrão | O que faz |
|---|---|---|
| `config` | `tlsvigil.toml` | O arquivo de configuração, relativo ao workspace |
| `lang` | vazio | O idioma do relatório, `en` ou `pt-BR`; vazio mantém o `lang` do arquivo |
| `notify` | `true` | `true` entrega aos canais de `[notifications]` e guarda o estado; `false` só verifica |
| `fail-on-attention` | `false` | `true` faz o passo falhar quando algo pede atenção, mesmo depois de entregue |
| `github-token` | `${{ github.token }}` | O token do canal `github` (`GITHUB_TOKEN`) |
| `telegram-token` | vazio | O token do bot do canal `telegram` (`TLSVIGIL_TELEGRAM_TOKEN`) |
| `telegram-chat-id` | vazio | O chat do canal `telegram` (`TLSVIGIL_TELEGRAM_CHAT_ID`) |
| `smtp-username` | vazio | O usuário do canal `smtp` (`TLSVIGIL_SMTP_USERNAME`) |
| `smtp-password` | vazio | A senha dele (`TLSVIGIL_SMTP_PASSWORD`) |
| `smtp-from` | vazio | O remetente, um endereço sem nome de exibição (`TLSVIGIL_SMTP_FROM`) |
| `smtp-to` | vazio | Os destinatários, separados por vírgula (`TLSVIGIL_SMTP_TO`) |

| Saída | O que guarda |
|---|---|
| `exit-code` | O código de saída da verificação, de `0` a `4`; `2` também para uma entrada inválida ou um runner sem Python 3.12 ou mais novo |

Cada entrada de canal vira a variável entre parênteses, a de
[Segredos e endereços](#segredos-e-endereços), então o valor dela deve vir dos segredos do
repositório (`${{ secrets.TLSVIGIL_TELEGRAM_TOKEN }}`), nunca escrito no workflow. Uma entrada
vazia não define nada. A Action esconde os tokens e a senha no log, e o relatório que ela escreve
no log e no resumo fica cercado, então um texto vindo de um certificado ou de uma resposta RDAP
nunca é lido como comando do workflow nem como Markdown.

### O estado e a cor de uma execução

O arquivo de estado fica no cache do Actions: cada execução restaura a entrada mais nova e salva a
sua. Sem ele, a execução seguinte é a primeira e dá cada aviso atual mais uma vez; nada fica
escondido. O GitHub apaga uma entrada de cache que passa 7 dias sem uso, então um agendamento
pausado por mais tempo recomeça desse jeito.

O passo segue o [código de saída](#linha-de-comando) da verificação. `0` e `1` ficam verdes: um
aviso, entre eles um host que não respondeu, é entregue, não é uma falha, e
`fail-on-attention: true` deixa o `1` vermelho. `2` (nada foi conferido: a configuração ou uma
entrada está errada), `3` (um bug) e `4` (uma notificação não foi entregue) ficam vermelhos, e o
resumo da execução diz qual.

### Limites da plataforma

- **Uma execução agendada pode atrasar** e, com o GitHub sobrecarregado, ser descartada; a do dia
  seguinte põe em dia.
- **Um repositório público sem atividade por 60 dias** tem o agendamento desligado; religue na aba
  **Actions**.
- **Um fork começa com os workflows agendados desligados,** e os segredos do original não chegam a
  ele.
- **Num repositório privado,** cada execução gasta minutos da cota de Actions do plano; uma
  verificação leva cerca de um minuto.
- **Um runner próprio (self-hosted)** precisa de Python 3.12 ou mais novo no path, ou de um passo
  `actions/setup-python` antes deste.

## TLSVigil e outras ferramentas

| | TLSVigil | [Uptime Kuma](https://github.com/louislam/uptime-kuma) | [Red Sift Certificates Lite](https://redsift.com/) | [action-check-domain](https://github.com/codex-team/action-check-domain) |
|---|---|---|---|---|
| Forma | Uma CLI e uma GitHub Action, só com a biblioteca padrão | Um serviço de monitoramento que você hospeda | Um serviço hospedado | Uma GitHub Action |
| Onde roda | No seu repositório do GitHub, ou em qualquer máquina com Python | Num servidor seu | Na nuvem do fornecedor | No seu repositório do GitHub |
| Vencimento do certificado | Sim | Sim | Sim, até 250 certificados no plano grátis | Sim |
| Vencimento do domínio | Sim, por RDAP | Sim | Não | Sim |
| Como avisa | Uma issue que se atualiza e se fecha sozinha; Telegram ou e-mail se você quiser | Mais de 90 serviços de notificação | E-mail, 7 dias antes | Saídas para o passo seguinte; o exemplo abre uma issue com outra Action |

_Comparação conferida nas páginas oficiais em novembro de 2026._

Se você já usa o Uptime Kuma, continue com ele: ele também vigia se o site está no ar. O TLSVigil é
para quando as datas de vencimento bastam e você prefere não manter um servidor rodando só para
elas.

## Escopo e limitações

O TLSVigil é pequeno de propósito. Para cada limite abaixo, o que usar no lugar.

- **Alguns domínios de topo não têm serviço RDAP.** O bootstrap da IANA não lista nenhum para
  `.de`, `.io`, `.co`, `.eu`, `.cn`, `.ru` e `.jp`, entre outros, então o vencimento desses
  domínios não é conferido; os certificados deles continuam sendo. *No lugar:* ligue os lembretes
  de renovação e a renovação automática do seu registrador.
- **Alguns registros deixam a data de fora.** O RDAP não exige o evento `expiration`, e um
  registro sem ele não gera aviso de domínio; o resultado diz que a data é desconhecida. *No
  lugar:* o mesmo do item acima.
- **Um certificado revogado não é detectado.** A verificação confere a cadeia e o nome e lê a data
  de vencimento, mas não pergunta à autoridade certificadora (OCSP ou CRL) se o certificado foi
  revogado antes dessa data. *No lugar:* um monitor que confira revogação, ou a página da sua conta
  na autoridade certificadora.
- **Uma porta que começa em texto puro não é conferida.** A verificação fala TLS desde o primeiro
  byte, como nas portas 443, 465 ou 993; um servidor que começa em texto puro e passa para TLS com
  STARTTLS (SMTP na 25 ou na 587, IMAP na 143, XMPP) termina como erro de TLS. *No lugar:* aponte
  para a porta de TLS implícito do mesmo servidor, quando houver (465 para SMTP, 993 para IMAP).
- **Ele vê o que a internet vê.** A verificação roda num runner hospedado pelo GitHub, então um
  host que só se alcança de dentro da sua rede fica de fora. *No lugar:* rode a linha de comando
  numa máquina dentro dessa rede, pelo `cron` ou por uma tarefa agendada.
- **O agendamento é de melhor esforço.** O GitHub pode começar um workflow agendado com atraso, ou
  pular uma execução quando os runners estão sobrecarregados. *No lugar:* mantenha o limite de
  aviso em vários dias, para que uma execução perdida nunca custe o aviso.
- **Ele não é monitor de disponibilidade.** Não diz que um site caiu, só que um certificado ou um
  domínio está para vencer. *No lugar:* o [Uptime Kuma](https://github.com/louislam/uptime-kuma)
  num servidor seu.

## Desenvolvimento

O Python 3.12 é o piso (o CI testa o 3.12 e a versão estável mais nova). O portão de qualidade
roda antes de cada commit por um hook versionado:

```bash
# Ative o hook de pre-commit uma vez por clone
git config core.hooksPath .githooks

# Lint (o hook e o CI rodam os mesmos quatro passos, nesta ordem)
uv run ruff check .

# Confira a formatação
uv run black --check .

# Confira os tipos (modo estrito)
uv run mypy

# Rode os testes com cobertura
uv run pytest --cov
```

Cada tecnologia da stack, e o motivo da escolha, está explicada em [docs/stack.md](docs/stack.md)
(em inglês). As funcionalidades são especificadas em [specs/](specs/) antes de serem construídas.

Contribuições são bem-vindas: veja o [CONTRIBUTING.md](CONTRIBUTING.md) (em inglês). Para relatar
uma vulnerabilidade no próprio TLSVigil, use o canal privado descrito no
[SECURITY.md](SECURITY.md), nunca uma issue pública.

## Licença

[Apache License 2.0](LICENSE).
