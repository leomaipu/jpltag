"""
Pruebas de motor.agenda.asignar_agenda: orden de asignación (N° de parte
mínimo primero) y comportamiento cuando se agotan los cupos.
"""

import unittest

from motor.agenda import SLOT_SIN_AGENDA, asignar_agenda


def grupo(nros_parte):
    return {"causas": [{"nroParte": n} for n in nros_parte]}


class TestAsignarAgenda(unittest.TestCase):
    def test_asigna_en_orden_de_nro_parte_minimo(self):
        grupos = {
            "BBBB22": grupo(["1003"]),
            "AAAA11": grupo(["1001", "1005"]),
            "CCCC33": grupo(["1002"]),
        }
        agenda = [("día 1", "09:00"), ("día 1", "09:15"), ("día 2", "09:00")]
        slots, sin_cupo = asignar_agenda(grupos, agenda)
        self.assertEqual(slots["AAAA11"], ("día 1", "09:00"))
        self.assertEqual(slots["CCCC33"], ("día 1", "09:15"))
        self.assertEqual(slots["BBBB22"], ("día 2", "09:00"))
        self.assertEqual(sin_cupo, [])

    def test_patentes_sin_cupo_quedan_a_coordinar(self):
        grupos = {"AAAA11": grupo(["1001"]), "BBBB22": grupo(["1002"])}
        slots, sin_cupo = asignar_agenda(grupos, agenda=[("día 1", "09:00")])
        self.assertEqual(slots["AAAA11"], ("día 1", "09:00"))
        self.assertEqual(slots["BBBB22"], SLOT_SIN_AGENDA)
        self.assertEqual(sin_cupo, ["BBBB22"])

    def test_agenda_vacia_deja_todo_sin_cupo(self):
        grupos = {"AAAA11": grupo(["1001"])}
        slots, sin_cupo = asignar_agenda(grupos, agenda=[])
        self.assertEqual(slots["AAAA11"], SLOT_SIN_AGENDA)
        self.assertEqual(sin_cupo, ["AAAA11"])

    def test_nro_parte_no_numerico_no_rompe_el_orden(self):
        """Un N° de parte no numérico (dato sucio) se manda al final del
        orden en vez de hacer fallar la asignación."""
        grupos = {
            "AAAA11": grupo(["no-es-numero"]),
            "BBBB22": grupo(["1001"]),
        }
        agenda = [("día 1", "09:00"), ("día 1", "09:15")]
        slots, sin_cupo = asignar_agenda(grupos, agenda)
        self.assertEqual(slots["BBBB22"], ("día 1", "09:00"))
        self.assertEqual(slots["AAAA11"], ("día 1", "09:15"))


if __name__ == "__main__":
    unittest.main()
