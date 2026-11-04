from types import SimpleNamespace

import pytest

from zelador import discos

GB = 1024**3


def particao(dispositivo, ponto, fstype="NTFS", opts="rw,fixed"):
    return SimpleNamespace(device=dispositivo, mountpoint=ponto, fstype=fstype, opts=opts)


@pytest.fixture
def falso_psutil(monkeypatch):
    """psutil com partições e usos que cada teste define."""
    estado = SimpleNamespace(particoes=[], usos={})

    def disk_usage(ponto):
        uso = estado.usos[ponto]
        if isinstance(uso, Exception):
            raise uso
        return uso

    monkeypatch.setattr(discos.psutil, "disk_partitions", lambda all=False: estado.particoes)
    monkeypatch.setattr(discos.psutil, "disk_usage", disk_usage)
    return estado


def test_calcula_o_percentual(falso_psutil):
    falso_psutil.particoes = [particao("C:\\", "C:\\")]
    falso_psutil.usos = {"C:\\": SimpleNamespace(total=200 * GB, used=50 * GB)}

    (disco,) = discos.coletar_discos()

    assert disco == discos.UsoDisco("C:\\", "C:\\", "NTFS", 200 * GB, 50 * GB, 25.0)


def test_ignora_o_que_nao_e_volume_de_dados(falso_psutil):
    uso = SimpleNamespace(total=100 * GB, used=10 * GB)
    falso_psutil.particoes = [
        particao("D:\\", "D:\\", fstype="", opts="cdrom"),          # leitor sem mídia
        particao("E:\\", "E:\\", fstype="CDFS", opts="ro,cdrom"),   # CD com mídia
        particao("F:\\", "F:\\", fstype=""),                        # sem sistema de arquivos
        particao("G:\\", "G:\\"),                                   # acesso negado
        particao("H:\\", "H:\\"),                                   # volume vazio (total 0)
        particao("C:\\", "C:\\"),                                   # o único bom
        particao("C:\\", "C:\\"),                                   # repetido
    ]
    falso_psutil.usos = {
        "E:\\": uso, "F:\\": uso, "D:\\": uso,
        "G:\\": PermissionError("negado"),
        "H:\\": SimpleNamespace(total=0, used=0),
        "C:\\": uso,
    }

    assert [d.ponto_de_montagem for d in discos.coletar_discos()] == ["C:\\"]


def test_mantem_a_ordem_do_sistema(falso_psutil):
    uso = SimpleNamespace(total=GB, used=GB // 2)
    falso_psutil.particoes = [particao("C:\\", "C:\\"), particao("D:\\", "D:\\")]
    falso_psutil.usos = {"C:\\": uso, "D:\\": uso}

    assert [d.dispositivo for d in discos.coletar_discos()] == ["C:\\", "D:\\"]


def test_sem_nenhuma_particao_devolve_vazio(falso_psutil):
    assert discos.coletar_discos() == ()


def test_percentual_com_total_zero_nao_divide_por_zero():
    assert discos._percentual(0, 0) == 0.0
    assert discos._percentual(1, 3) == 33.3


# ------------------------------- psutil de verdade (sem simulação)


def test_discos_reais_tem_valores_plausiveis():
    lista = discos.coletar_discos()

    assert len(lista) >= 1
    for disco in lista:
        assert 0 < disco.usado <= disco.total
        assert 0.0 <= disco.percentual <= 100.0
        assert disco.ponto_de_montagem and disco.sistema_de_arquivos
