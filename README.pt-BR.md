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
> README descreve a ferramenta como ela está especificada em [specs/](specs/). Até o primeiro
> lançamento, só `tlsvigil --version` roda.

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

# Confira que o comando roda (o único que existe até agora)
uv run tlsvigil --version
```

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
