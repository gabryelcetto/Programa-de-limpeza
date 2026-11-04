"""Desenha o ícone do Zelador (assets/zelador.ico): uma figura varrendo, em traço simples.

Desenho próprio, feito com formas geométricas (círculo, linhas e um trapézio), sem usar nenhuma
imagem pronta. Requer Pillow (só para gerar o ícone; não é dependência do programa):
    pip install pillow
    python scripts/gerar-icone.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

TAMANHO = 1024  # desenha grande e reduz: as bordas ficam suaves
FUNDO = (38, 50, 68, 255)  # azul-ardósia
TRACO = (240, 244, 248, 255)
VASSOURA = (242, 169, 59, 255)  # destaque: as cerdas
TAMANHOS_DO_ICO = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def traco(d: ImageDraw.ImageDraw, a: tuple, b: tuple, largura: int, cor=TRACO) -> None:
    """Linha com pontas arredondadas (o ImageDraw não faz isso sozinho)."""
    d.line((*a, *b), fill=cor, width=largura)
    r = largura / 2
    for x, y in (a, b):
        d.ellipse((x - r, y - r, x + r, y + r), fill=cor)


def desenhar() -> Image.Image:
    img = Image.new("RGBA", (TAMANHO, TAMANHO), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((32, 32, TAMANHO - 32, TAMANHO - 32), radius=200, fill=FUNDO)

    # figura (levemente inclinada para a direita, em movimento de varrer)
    d.ellipse((400, 170, 520, 290), fill=TRACO)  # cabeça
    traco(d, (470, 320), (440, 590), 64)  # tronco
    traco(d, (470, 350), (600, 500), 46)  # braço da frente (segura o cabo)
    traco(d, (450, 360), (385, 510), 42)  # braço de trás
    traco(d, (440, 590), (385, 820), 54)  # perna de trás
    traco(d, (440, 590), (525, 820), 54)  # perna da frente
    traco(d, (385, 835), (335, 840), 42)  # pé de trás
    traco(d, (525, 835), (590, 840), 42)  # pé da frente

    # vassoura: cabo na diagonal, das mãos até o chão, e cerdas em trapézio
    traco(d, (610, 330), (760, 800), 34)
    d.polygon([(730, 800), (830, 800), (900, 900), (700, 900)], fill=VASSOURA)

    return img


def main() -> None:
    destino = Path(__file__).resolve().parent.parent / "assets" / "zelador.ico"
    destino.parent.mkdir(exist_ok=True)
    img = desenhar()
    img.save(destino, format="ICO", sizes=TAMANHOS_DO_ICO)
    img.resize((256, 256), Image.LANCZOS).save(destino.with_suffix(".png"))
    print(f"Ícone gravado em {destino}")


if __name__ == "__main__":
    main()
