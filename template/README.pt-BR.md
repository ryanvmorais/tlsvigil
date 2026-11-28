# Modelo do TLSVigil

Uma verificação diária dos seus certificados TLS e registros de domínio, feita pelo GitHub Actions
no seu próprio repositório, que mantém uma issue aberta enquanto algo estiver perto de vencer.

**Idioma:** [English](README.md) · Português (Brasil)

Todo dia o workflow roda o [TLSVigil](https://github.com/ryanvmorais/tlsvigil) nos hosts e
domínios do [tlsvigil.toml](tlsvigil.toml). A data do certificado vem de um handshake TLS
verificado; a do domínio, do RDAP, a API pública dos registros. Quando algum deles pede atenção, a
execução abre uma issue neste repositório e comenta nela conforme os dias passam; quando tudo volta
ao normal, fecha a issue. Sem servidor, sem conta e sem segredo para começar: a issue é escrita com
o token do próprio workflow.

## Configure em quatro passos

1. **Crie o seu repositório.** Clique em **Use this template** → **Create a new repository**.
   Público ou privado, os dois funcionam.
2. **Diga o que verificar.** Edite o `tlsvigil.toml`: troque `example.com` pelos seus hosts e
   domínios, troque `lang = "en"` por `lang = "pt-BR"` para receber tudo em português e faça o
   commit na `main`. O commit já dispara uma verificação; abra a aba **Actions** para vê-la e o
   resumo dela para o resultado.
3. **Acompanhe o repositório.** Clique em **Watch** → **All Activity**. A issue é aberta por
   `github-actions[bot]`, e o GitHub manda os e-mails e as notificações dela só a quem acompanha o
   repositório; "All Activity" também traz os pull requests de uma release nova do TLSVigil. Num
   repositório movimentado, **Custom** → **Issues** é mais silencioso.
4. **Opcional: Telegram ou e-mail.** Acrescente o canal em `channels` no `tlsvigil.toml`, cadastre
   os segredos dele em **Settings** → **Secrets and variables** → **Actions** e tire o comentário
   das linhas dele em `.github/workflows/tlsvigil.yml`.

## O que a cor de uma execução quer dizer

| Execução | Significado |
|---|---|
| Verde | Todas as verificações foram feitas e todas as notificações entregues; um aviso fica na issue. |
| Vermelha | Uma verificação não pôde rodar (um erro no `tlsvigil.toml`, por exemplo) ou uma notificação não foi entregue; o resumo da execução diz qual. |

Um certificado perto de vencer é uma issue, não uma execução vermelha; então uma execução vermelha
sempre quer dizer que a própria verificação precisa de você.

## Limites do GitHub Actions

- Uma execução agendada pode começar atrasada e, com o GitHub sobrecarregado, ser descartada; a do
  dia seguinte põe em dia.
- Num repositório público, o GitHub desliga o agendamento depois de 60 dias sem atividade; religue
  na aba **Actions**.
- Crie o repositório pelo modelo, não como fork: um fork começa com os workflows agendados
  desligados, e os segredos não chegam a ele.
- Num repositório privado, cada execução gasta minutos da cota de Actions do seu plano; uma
  verificação leva cerca de um minuto.

## Mais

Todas as chaves do `tlsvigil.toml`, as entradas da Action e a linha de comando estão documentadas
no [README do TLSVigil](https://github.com/ryanvmorais/tlsvigil/blob/main/README.pt-BR.md).
