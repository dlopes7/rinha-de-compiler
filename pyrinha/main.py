"""
Compilador para Rinha, a linguagem funcional da rinha de compiladores.

Vou fazer este projeto buscando ser educativo, como provocado pelo Hillel Wayne
(https://buttondown.email/hillelwayne/archive/educational-codebases/). Seções
como esta são voltadas para explicar a lógica por trás do design.
"""

from enum import Enum
import json
from pathlib import Path
from textwrap import indent

"""
Eu uso _muito_ a biblioteca 'attrs' para definir classes. Ela é a precursora das
dataclasses, e possui mais capacidades além da biblioteca padrão que justificam
usá-la (https://www.attrs.org/en/stable/why.html#data-classes).

A biblioteca 'cattrs' é uma companheira de 'attrs' para conversão de classes para
dicts.
"""

from attrs import field, define, frozen, evolve
from cattrs import Converter

"""
Nesta seção, estou definindo classes para representar os atributos de AST da 
linguagem Rinha conforme a especificação:
https://github.com/brunokim/rinha-de-compiler/blob/main/SPECS.md

Cada 'Node' da AST possui um método __str__ para facilitar o debug. Eu quis
que esse método emitisse uma representação mais próxima da linguagem original,
portanto tenho que me preocupar com indentação. Para isso, uso a função
textwrap.indent da biblioteca padrão.

Um defeito deste método de serialização -- e da "desserialização" executada
pela biblioteca cattrs -- é que ela opera por chamadas recursivas, onde uma AST
mais extrema pode causar um RecursionError. Por exemplo, uma operação como

    1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + ...

Iria produzir uma AST muito profunda:

    Binary(
        Int(1),
        ADD,
        Binary(
            Int(1),
            ADD,
            Binary(
                Int(1),
                ADD,
                ...)))

Temos que pensar que a AST é uma entrada de usuário, e que nosso código pode
ser alvo de agentes maliciosos. Mais para frente irei implementar métodos
iterativos para operar sobre a AST, que são mais robustos.
"""

# ---- AST ----


@frozen
class Loc:
    start: int
    end: int
    filename: str


@frozen
class Node:
    location: Loc


@frozen
class Term(Node):
    pass


@frozen
class File(Node):
    name: str
    expression: Term

    def __str__(self):
        return str(self.expression)


@frozen
class Symbol(Node):
    text: str

    def __str__(self):
        return self.text


@frozen
class Let(Term):
    name: Symbol
    value: Term
    next: Term

    def __str__(self):
        return f"""\
let {self.name} = {self.value};
{self.next}"""


"""
A classe 'Function' usa mais uma feature da biblioteca 'attrs', para
definir propriedades extras de um campo.

Em geral, basta declarar um campo usando a sintaxe de tipos de Python
para que 'attrs' inclua ele no __init__, mas usando field() podemos
especificar um valor default, ou uma factory para gerá-lo. A factory
deve ser uma função com 0 argumentos, então podemos usar as funções
list(), tuple(), dict(), etc.

Podemos também especificar um converter, que nos permite aceitar um
tipo mais genérico no __init__ e garantir que internamente temos o
tipo certo.
"""


@frozen
class Function(Term):
    value: Term
    parameters: tuple[Symbol, ...] = field(factory=tuple, converter=tuple)

    def __str__(self):
        value = indent(str(self.value), "  ")
        params = ", ".join(str(param) for param in self.parameters)
        return f"""\
fn ({params}) => {{
{value}
}}"""


@frozen
class If(Term):
    condition: Term
    then: Term
    otherwise: Term

    def __str__(self):
        then = indent(str(self.then), "  ")
        otherwise = indent(str(self.otherwise), "  ")
        return f"""\
if {self.condition} {{
{then}
}} else {{
{otherwise}
}}"""


"""
A classe 'Operator' descreve um operador binário. O campo 'precedence'
é usado para definir se uma operação precisa ou não de parênteses
ao ser escrita. Por exemplo, a operação

    Binary(Int(1), MUL, Binary(Int(2), ADD, Int(3)))

precisa ser escrita como
    
    1 * (2 + 3)

mas a operação

    Binary(Int(1), ADD, Binary(Int(2), MUL, Int(3)))

pode ser escrita como

    1 + 2 * 3

Portanto, se ao escrever um operador, ele tiver uma precedência menor do
que a do operador atual, devemos usar parênteses.

O campo 'assoc' pretende determinar se operações com operadores de mesma
precedência deve ou não usar parênteses. Para os operadores aritméticos
é esperado que não, mas para operadores lógicos pode ser necessário.
Considere por exemplo, a diferença entre estas expressões:

    Binary(Int(1), EQ, Binary(Int(2), EQ, Var("true")))
    Binary(Binary(Int(1), EQ, Int(2)), EQ, Var("true"))

Eu acho melhor que elas sejam sempre serializadas como

    1 == (2 == true)
    (1 == 2) == true
"""


@frozen
class Operator:
    token: str
    precedence: int
    assoc: bool = True


"""
Os valores de precedência são arbitrários. Eu comecei com 30 e fui
adicionando os outros pensando se eu gostaria ou não que uma operação
combinada tivesse parênteses.

Usar valores separados por 10 ajuda a enfiar outros valores no meio
depois. Essa técnica vem desde o tempo dos cartões perfurados, quando
cada um era numerado. Se você quisesse adicionar um cartão no meio de
dois que você já tinha, bastava usar um número intermediário aos já
utilizados.
"""


class BinaryOp(Enum):
    ADD = Operator("+", 30)
    SUB = Operator("-", 30)
    MUL = Operator("*", 40)
    DIV = Operator("/", 40)
    REM = Operator("%", 40)
    EQ = Operator("==", 20, assoc=False)
    NEQ = Operator("!=", 20, assoc=False)
    LT = Operator("<", 20)
    GT = Operator(">", 20)
    LTE = Operator("<=", 20)
    GTE = Operator(">=", 20)
    AND = Operator("&", 10)
    OR = Operator("|", 5)

    # TODO: NOT não é uma operação binária
    # https://github.com/aripiprazole/rinha-de-compiler/issues/10
    NOT = Operator("!", 25)


@frozen
class Binary(Term):
    lhs: Term
    op: BinaryOp
    rhs: Term

    def __str__(self):
        self_precedence = self.op.value.precedence
        lhs_precedence = (
            self.lhs.op.value.precedence if isinstance(self.lhs, Binary) else 99
        )
        rhs_precedence = (
            self.rhs.op.value.precedence if isinstance(self.rhs, Binary) else 99
        )

        lhs = str(self.lhs)
        if lhs_precedence < self_precedence:
            lhs = f"({lhs})"

        rhs = str(self.rhs)
        if rhs_precedence < self_precedence:
            rhs = f"({rhs})"

        return f"{lhs} {self.op.value.token} {rhs}"


@frozen
class Call(Term):
    callee: Term
    arguments: tuple[Term, ...] = field(factory=tuple, converter=tuple)

    def __str__(self):
        callee = str(self.callee)
        if not isinstance(self.callee, Var):
            callee = f"({callee})"
        args = ", ".join(str(arg) for arg in self.arguments)
        return f"{callee}({args})"


@frozen
class Print(Term):
    value: Term

    def __str__(self):
        return f"print ({self.value})"


@frozen
class Var(Term):
    text: str

    def __str__(self):
        return self.text


@frozen
class Int(Term):
    value: int

    def __str__(self):
        return str(self.value)


@frozen
class Str(Term):
    value: str

    def __str__(self):
        return repr(self.value)


"""
Estas variáveis visam criar uma relação entre (nome da class Term) -> classe.
Isto é útil para podermos converter um objeto serializado da AST, que possui
um membro "kind" descrevendo a classe, para a classe em si.

Talvez fosse possível construir essa lista com alguma mágica de __new__ dentro
de Term, o que garantiria que uma nova classe Term sempre estará presente, mas
preferi fazer desse jeito repetitivo e simples.
"""

term_classes = [Let, Function, If, Binary, Call, Print, Var, Int, Str]
term_by_kind = {cls.__name__.lower(): cls for cls in term_classes}

# ---- Read AST ----

converter = Converter()

"""
A biblioteca 'cattrs' precisa de um pouco de customização antes de conseguir
desserializar ("structure") a AST da Rinha.

Em geral, ela utiliza os tipos incluídos na definição das classes para determinar
qual método usar. Por exemplo, considere o dict:

    {
        "kind": "Let",
        "name": {"text": "x"},
        "value": {"kind": "Var", "text": "true"},
    }

Se já estamos estruturando um Let, ao chegar no campo 'name' sabemos pela anotação
de tipos que este deve ser um Symbol. Contudo, no campo 'value' a anotação contém
Term, que é uma classe abstrata. Queremos neste momento ler o campo 'kind' do dict
para decidir estruturar um Var.

É isso que a função 'structure_generic_term' faz abaixo, onde usamos o dict
'term_by_kind' definido anteriormente.
"""


def structure_generic_term(obj, t):
    cls_name = obj["kind"].lower()
    return converter.structure(obj, term_by_kind[cls_name])


"""
Ao registrar a função 'structure_generic_term' para conversão de Terms, precisamos
limitá-la condicionalmente _apenas_ ao tipo Term, sem incluir seus subtipos.

Isto é necessário porque a função internamente chama structure novamente. Porém, a classe
passada como parâmetro também é um Term, e 'structure_generic_term' é chamada novamente!
Para sair do loop infinito, basta passar o predicado 'lambda cls: cls == Term', que
faz com que o hook só seja invocado quando cls for igual a, e não um subtipo de Term.

Portanto, a chamada a structure usa uma classe concreta como Let, Binary, etc., que
irá utilizar o mecanismo interno de 'cattrs' para desserialização.
"""

converter.register_structure_hook_func(lambda cls: cls == Term, structure_generic_term)


"""
Precisamos também customizar a desserialização de BinaryOp, porque por padrão 'cattrs'
utiliza o _valor_ do Enum como chave para a instância, e nós queremos usar o _nome_.
"""

converter.register_structure_hook(BinaryOp, lambda obj, t: BinaryOp[obj.upper()])

# ---- Values ----

"""
Até agora nós modelamos apenas a representação estática de um programa, na forma de
nós da árvore de sintaxe abstrata. Agora vamos modelar os valores de _runtime_ do
programa, que serão executados pelo interpretador.
"""


@frozen
class Value:
    pass


@frozen
class Literal(Value):
    "Literal contém um valor de Python wrapped como um valor de interpretador."
    x: int | str | bool

    def __str__(self):
        return str(self.x)


"""
Durante a execução, nós precisamos manter um registro de quais variáveis já foram
definidas, e associdas a qual valor. Por exemplo,

    let x = 10;
    print (x)

Na linha #2, a variável 'x' precisa estar associada ao valor 10, para sabermos o que
escrever na tela. Este registro se chama "environment", que modelamos no Env abaixo.

Nossa definição de Env copia todos os valores definidos anteriormente para um novo
dicionário e inclui (ou sobrescreve) novas associações. Um novo environment é criado
a cada 'Let', e ao invocar uma função, onde os parâmetros são associados aos valores
concretos dos argumentos. Veremos em mais detalhes no Interpretador.
"""


@frozen
class Env:
    # Apesar da classe ser marcada como 'frozen', ou imutável, isso não se estende
    # aos seus campos. Para garantir que o dict abaixo não seja modificado, precisamos
    # confiar apenas na nossa disciplina.
    values: dict[str, Value] = field(factory=dict, converter=dict)

    @classmethod
    def global_env(cls):
        return Env(
            {
                "true": Literal(True),
                "false": Literal(False),
            }
        )

    def with_values(self, extra: dict[str, Value]) -> Value:
        "Cria um novo Env com base no atual, e contendo as associações em extra"
        values = dict(self.values)
        values.update(extra)
        return Env(values)


"""
Além dos valores atômicos descritos anteriormente, também precisamos modelar uma
função anônima em tempo de execução. Funções podem ser passadas como parâmetros,
associadas a uma variável, e até impressas com 'print'.

Em runtime, uma função captura o environment onde ela foi declarada, para ser
pura e imutável. Considere por exemplo:

    let x = 1;                   // Env: {x: 1}
    let f = fn (a) { a + x };    // Env: {x: 1, f: <Closure#... fn (a)>}
    let x = 2;                   // Env: {x: 2, f: <Closure#... fn (a)>}
    print(f(10))

Ao chamar a função na linha 4, esperamos que imprima '11', tendo capturado o
valor de x definido na linha 1.
"""


@frozen
class Closure(Value):
    function: Function
    env: Env

    def __str__(self):
        ptr = hex(id(self))[-6:]
        return f"<Closure#{ptr} fn ({args})>"


# ---- Interpreter ----

"""
Agora chegamos à parte divertida: interpretar o código!

Como a linguagem Rinha é uma linguagem funcional e pura (excetuando 'print'),
vamos usar isso a nosso favor no design do interpretador. Por exemplo, o
interpretador se baseia somente em _calcular valores_, computando uma saída
a partir das entradas.

Vamos escrever primeiro uma função 'evaluate0' que é bem simples e
ineficiente, mas que pelo menos vai servir de padrão para desenvolvimentos
mais complexos posteriores. Essa função será chamada recursivamente quando for
necessário calcular sub-valores.

É interessante que quase todas as expressões podem produzir

Uma das facilidades
é não existir a possibilidade de uma variável mudar de valor, o que complicaria
um pouco a implementação de Closure. Podemos apenas referenciar o environment
presente onde uma função é declarada, que todos os valores serão os mesmos em
todos os instantes onde ela será executada.
"""


class ExecutionError(Exception):
    pass


def run_file(file: File):
    return evaluate(Env.global_env(), file.expression)


def evaluate(env: Env, term: Term) -> tuple[Env, Value]:
    match term:
        case Int(_, value):
            return env, Literal(value)
        case Str(_, value):
            return env, Literal(value)
        case Var(_, text):
            if text not in env.values:
                raise ExecutionError(f"unknown variable '{text}'")
            return env, env.values[text]
        case Let(_, name, value, next):
            _, val = evaluate(env, value)
            next_env = env.with_values({name.text: val})
            if isinstance(val, Closure):
                # Let of fn is recursive, so closure must be able to reference itself.
                new_val = evolve(val, env=next_env)
                next_env.values[name.text] = new_val
            return evaluate(next_env, next)
        case Function():
            return env, Closure(term, env)
        case If(_, condition, then, otherwise):
            _, cond = evaluate(env, condition)
            match cond:
                case Literal(True):
                    return evaluate(env, then)
                case Literal(False):
                    return evaluate(env, otherwise)
                case _:
                    raise ExecutionError(f"condition in 'if' is not boolean")
        case Binary(_, lhs, op, rhs):
            _, lhs = evaluate(env, lhs)
            _, rhs = evaluate(env, rhs)
            match lhs, rhs:
                case Literal(), Literal():
                    pass
                case _:
                    raise ExecutionError(
                        f"Invalid operands for '{op.value.token}': {lhs}, {rhs}"
                    )

            match op.value.token, lhs.x, rhs.x:
                case "+", int(), int():
                    return env, Literal(lhs.x + rhs.x)
                case "+", str(), str():
                    return env, Literal(lhs.x + rhs.x)
                case "-", int(), int():
                    return env, Literal(lhs.x - rhs.x)
                case "*", int(), int():
                    return env, Literal(lhs.x * rhs.x)
                case "/", int(), int():
                    return env, Literal(lhs.x // rhs.x)
                case "%", int(), int():
                    return env, Literal(lhs.x % rhs.x)
                case "==", _, _:
                    return env, Literal(lhs == rhs)
                case "!=", _, _:
                    return env, Literal(lhs != rhs)
                case "<", int(), int():
                    return env, Literal(lhs.x < rhs.x)
                case "<", str(), str():
                    return env, Literal(lhs.x < rhs.x)
                case ">", int(), int():
                    return env, Literal(lhs.x > rhs.x)
                case ">", str(), str():
                    return env, Literal(lhs.x > rhs.x)
                case "<=", int(), int():
                    return env, Literal(lhs.x <= rhs.x)
                case "<=", str(), str():
                    return env, Literal(lhs.x <= rhs.x)
                case ">=", int(), int():
                    return env, Literal(lhs.x >= rhs.x)
                case ">=", str(), str():
                    return env, Literal(lhs.x >= rhs.x)
                case "&", bool(), bool():
                    return env, Literal(lhs.x and rhs.x)
                case "|", bool(), bool():
                    return env, Literal(lhs.x or rhs.x)
                case _:
                    raise ExecutionError(
                        f"Invalid operands for '{op.value.token}': {lhs}, {rhs}"
                    )
        case Call(_, callee, arguments):
            _, f = evaluate(env, callee)
            if not isinstance(f, Closure):
                raise ExecutionError(f"'{f}' is not callable")
            if len(arguments) != len(f.function.parameters):
                raise ExecutionError(
                    f"'{f.function}' called with {len(arguments)} arguments"
                )
            values = (evaluate(env, arg) for arg in arguments)
            params = {
                param.text: value
                for param, (_, value) in zip(f.function.parameters, values)
            }
            call_env = f.env.with_values(params)
            return evaluate(call_env, f.function.value)
        case Print(_, value):
            _, val = evaluate(env, value)
            print(val, end="")
            return env, val
        case _:
            raise ExecutionError(f"Unexpected type {type(term)}")


# ---- Main ----


def main(ast_obj):
    node = converter.structure(ast_obj, File)
    print(node)
    print()
    run_file(node)


if __name__ == "__main__":
    from argparse import ArgumentParser
    from pathlib import Path

    p = ArgumentParser()
    p.add_argument("file", type=Path, help="AST file to execute")
    args = p.parse_args()

    with args.file.open() as f:
        ast = json.load(f)
    main(ast)
