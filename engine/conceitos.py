"""Base de conceitos do FIDC — usada nos tooltips (help=) e no Guia.

Cada conceito traz: o que é, faixa típica de mercado (referência, não regra)
e como afeta a estrutura no simulador.
"""

CONCEITOS = {
    "pl_total": dict(
        titulo="Patrimônio líquido (PL) do fundo",
        resumo="Tamanho total do fundo: soma do capital de todas as classes "
               "de cotas, usado para comprar os direitos creditórios.",
        faixa="FIDCs viáveis em geral partem de R$ 30–50 mi; abaixo disso os "
              "custos fixos (administrador, custódia, auditoria) corroem o retorno.",
        efeito="Define a escala. No modelo, os custos são % do PL — fundos "
               "pequenos têm despesa proporcionalmente maior.",
    ),
    "senior": dict(
        titulo="Cota sênior",
        resumo="Classe com prioridade máxima na cascata: recebe rendimento e "
               "amortização antes das demais. É onde o banco investe.",
        faixa="Tipicamente 65–85% do PL. Quanto maior a fatia sênior, maior a "
               "alavancagem da estrutura e menor o colchão de proteção.",
        efeito="Aumentar a sênior reduz a subordinação e derruba o break-even "
               "— o comitê exige folga maior em carteiras mais arriscadas.",
    ),
    "mezanino": dict(
        titulo="Cota mezanino",
        resumo="Classe intermediária: recebe depois da sênior e antes da "
               "júnior. Absorve perdas que ultrapassem a júnior.",
        faixa="0–20% do PL. Nem toda estrutura tem; é usada para 'vender' "
               "parte do risco a investidores que aceitam mais retorno.",
        efeito="Protege a sênior (entra na frente dela na absorção de perdas) "
               "e reduz o capital que o originador precisa aportar na júnior.",
    ),
    "junior": dict(
        titulo="Cota júnior (subordinada)",
        resumo="Classe que absorve as primeiras perdas e fica com o residual "
               "(excesso de spread). Normalmente é integralizada pelo próprio "
               "originador/cedente — o 'skin in the game'.",
        faixa="Mercado costuma exigir 15–35%, calibrado pela perda esperada "
               "da carteira (regra de bolso: 2–3× a perda histórica).",
        efeito="É o colchão da sênior. No simulador, perdas acumuladas são "
               "comparadas com ela; se as perdas a atravessam, o mezanino e "
               "depois a sênior sofrem.",
    ),
    "taxa_senior": dict(
        titulo="Taxa-alvo da sênior (benchmark)",
        resumo="Remuneração prometida à cota sênior — em geral expressa como "
               "CDI + spread ou % do CDI. No modelo, use a taxa mensal "
               "equivalente ao cenário de CDI que você projeta.",
        faixa="Spreads comuns: CDI + 2% a 5% a.a. conforme risco da carteira, "
               "rating e liquidez.",
        efeito="Custo de funding da estrutura: quanto maior, mais caixa a "
               "cascata consome antes de sobrar residual para a júnior.",
    ),
    "taxa_mezanino": dict(
        titulo="Taxa-alvo do mezanino",
        resumo="Remuneração da classe intermediária — sempre acima da sênior, "
               "compensando a posição pior na cascata.",
        faixa="Tipicamente CDI + 4% a 8% a.a., entre a sênior e o retorno "
               "esperado da júnior.",
        efeito="Mesmo mecanismo da sênior: acumula sobre o saldo e é paga "
               "na amortização, depois da sênior.",
    ),
    "taxa_cessao": dict(
        titulo="Taxa de cessão (desconto)",
        resumo="Taxa com que o fundo compra os recebíveis do originador — o "
               "rendimento implícito da carteira. É a receita da estrutura.",
        faixa="Varia muito por ativo: duplicatas 1,5–3,5% a.m.; consignado e "
               "convênios menos; carteiras de maior risco, mais.",
        efeito="O motor do excesso de spread: taxa de cessão menos taxa-alvo "
               "das cotas menos perdas e custos = retorno da júnior.",
    ),
    "prazo_medio": dict(
        titulo="Prazo médio dos recebíveis",
        resumo="Tempo médio até o vencimento dos títulos comprados (duration "
               "da carteira).",
        faixa="Duplicatas: 30–90 dias; cartão: 30–330; consignado: anos.",
        efeito="Prazos curtos giram a carteira mais vezes na revolvência "
               "(mais receita, mais reinvestimento) e encurtam a amortização.",
    ),
    "inadimplencia": dict(
        titulo="Inadimplência (perda esperada)",
        resumo="Fração dos valores que vencem e não são pagos. No modelo, "
               "incide mensalmente sobre juros + principal vencidos.",
        faixa="Use a perda líquida histórica da carteira por safra — não o "
               "atraso pontual. Duplicatas saudáveis: 0,5–2% a.m. sobre "
               "vencimentos; valide com o aging real no módulo de carteira.",
        efeito="É o risco central. O stress multiplica essa taxa para testar "
               "até onde a estrutura aguenta (break-even).",
    ),
    "prepagamento": dict(
        titulo="Pré-pagamento",
        resumo="Fração do principal antecipada pelos sacados antes do "
               "vencimento.",
        faixa="Depende do ativo; 0,5–3% a.m. é comum em carteiras comerciais.",
        efeito="Acelera o retorno do caixa: reduz a exposição à perda, mas "
               "também o tempo em que a carteira rende a taxa de cessão.",
    ),
    "recuperacao": dict(
        titulo="Taxa de recuperação",
        resumo="Percentual dos créditos vencidos que a cobrança consegue "
               "recuperar depois do default.",
        faixa="Muito variável: 10–50% conforme garantias, coobrigação do "
               "cedente e qualidade da cobrança.",
        efeito="Reduz a perda líquida. Estruturas com coobrigação do cedente "
               "podem justificar taxas maiores — e subordinação menor.",
    ),
    "custos": dict(
        titulo="Custos do fundo",
        resumo="Despesas recorrentes: administração, gestão, custódia, "
               "auditoria, registradora, rating, taxas CVM/ANBIMA.",
        faixa="0,8–2,0% do PL a.a. — o peso relativo cai com a escala.",
        efeito="Saem do caixa antes de qualquer cota na cascata.",
    ),
    "stress": dict(
        titulo="Stress e break-even",
        resumo="O stress multiplica a inadimplência base para simular "
               "cenários adversos. O break-even é o maior múltiplo em que a "
               "sênior ainda recebe 100% do prometido.",
        faixa="Comitês costumam exigir que a estrutura resista a 3–5× a perda "
               "histórica; agências de rating usam lógica parecida por nota.",
        efeito="É o número que resume a segurança da sênior — e o principal "
               "argumento de venda na distribuição a terceiros.",
    ),
    "tir": dict(
        titulo="TIR por classe",
        resumo="Taxa interna de retorno anualizada dos fluxos de cada classe "
               "(aporte inicial vs pagamentos recebidos na simulação).",
        faixa="Sênior deve fechar próximo da taxa-alvo; a sub captura o "
               "residual — retornos altos compensam o risco de primeira perda.",
        efeito="Compare a TIR da sub com o custo de capital do originador: é "
               "o que define se a estrutura 'para em pé' para ele.",
    ),
    "sub_minima": dict(
        titulo="Subordinação mínima (gatilho / evento de avaliação)",
        resumo="Índice mínimo de subordinação dinâmica — (ativos − dívida "
               "das classes) / ativos — que o fundo deve manter. Se furar, "
               "dispara o evento de avaliação: a revolvência para e o caixa "
               "passa a amortizar as classes por senioridade.",
        faixa="Regulamentos costumam fixar o mínimo um pouco abaixo da "
              "subordinação inicial (ex.: inicial 20%, mínimo 12–15%), "
              "dando espaço para oscilação sem disparos falsos.",
        efeito="É a principal defesa dinâmica da sênior: interromper o "
               "reinvestimento cedo preserva caixa e eleva drasticamente o "
               "break-even — compare a simulação com e sem gatilho.",
    ),
    "rampa": dict(
        titulo="Rampa de integralização",
        resumo="Período em que o capital das cotas é chamado "
               "(integralizado) progressivamente, em vez de 100% já no "
               "mês 1. Cada classe só passa a render sobre o capital que "
               "já foi efetivamente chamado e investido em recebíveis.",
        faixa="Tipicamente de 3 a 12 meses, calibrado pela capacidade real "
              "de originação do cedente — compare com a 'rampa implícita "
              "pela capacidade' calculada no cadastro do originador.",
        efeito="Evita 'carry' negativo: sem rampa, o capital capturado e "
               "ainda não investido em recebíveis já está rendendo o "
               "benchmark prometido às cotas seniores — um custo que "
               "ninguém está cobrindo. Com rampa, o capital só é chamado "
               "(e só passa a render) na medida em que é deployado.",
    ),
    "carencia": dict(
        titulo="Carência",
        resumo="Período sem amortização de cotas: o caixa recebido é "
               "reinvestido na compra de novos direitos creditórios "
               "(revolvência) o tempo todo, em vez de pagar principal. "
               "A amortização começa logo depois que a carência termina.",
        faixa="Fundos fechados costumam ter 2 a 5 anos de carência antes "
              "da amortização programada.",
        efeito="Mais carência = mais tempo capturando spread via "
               "revolvência (melhor para a subordinação), mas também mais "
               "tempo exposto à inadimplência. Quantos ciclos de "
               "revolvência cabem no período depende do prazo médio dos "
               "recebíveis da carteira — carência de 12 meses com "
               "recebíveis de 2 meses de prazo médio permite ~6 ciclos.",
    ),
    "custo_inicial": dict(
        titulo="Custo inicial one-off",
        resumo="Custo único de estruturação — por exemplo, uma taxa de "
               "distribuição flat cobrada sobre o valor integralizado da "
               "cota sênior. Diferente dos custos recorrentes (gestão, "
               "custódia), sai do caixa uma vez só (ou diferido em alguns "
               "meses), não todo mês pelo prazo do fundo.",
        faixa="Taxas de distribuição costumam ficar entre 0,3% e 1,0% flat "
              "sobre o valor da(s) classe(s) distribuída(s).",
        efeito="Reduz o caixa disponível justamente nos primeiros meses, "
               "quando o fundo ainda está formando a carteira — diferir "
               "esse custo em mais meses suaviza o golpe de caixa em "
               "qualquer mês individual, sem mudar o custo total.",
    ),
    "prazo_maximo": dict(
        titulo="Prazo máximo do fundo",
        resumo="Teto legal de duração do fundo (a data-limite prevista no "
               "regulamento). Se a amortização natural ainda não tiver "
               "terminado até esse mês, o motor força a liquidação da "
               "carteira remanescente e paga a cascata com o que houver.",
        faixa="FIDCs de recebíveis comerciais costumam ter 24 a 60 meses "
              "de prazo total; 0 (sem teto) serve para checar quanto "
              "tempo a estrutura levaria naturalmente, sem essa restrição.",
        efeito="Se o teto cortar antes de todas as classes serem pagas, "
               "'Todas as classes íntegras?' aponta 'NÃO' — sinal de que "
               "o cronograma não cabe no prazo legal do fundo tal como "
               "parametrizado.",
    ),
    "ajuste_curva": dict(
        titulo="Ajuste de curva (p.p. aa)",
        resumo="Prêmio ou desconto somado ao CDI projetado antes de "
               "calcular as taxas atreladas a CDI (classes e, se "
               "escolhido, a taxa de cessão) — uma forma de aproximar a "
               "curva de juros futura real de mercado sem depender de "
               "CDI constante.",
        faixa="Normalmente pequeno (poucas dezenas de pontos-base) — "
              "reflete a diferença entre o CDI spot e a expectativa do "
              "mercado (DI futuro) para o prazo médio do fundo.",
        efeito="Positivo encarece o funding (CDI efetivo maior); negativo "
               "barateia. Zero (padrão) assume CDI plano pelo prazo todo.",
    ),
}


def ajuda(chave: str) -> str:
    """Texto curto para o parâmetro help= dos widgets."""
    c = CONCEITOS[chave]
    return f"{c['resumo']}\n\n**Faixa típica:** {c['faixa']}"
