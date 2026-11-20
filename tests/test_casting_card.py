import unittest

from app.casting_card import card_heading


class CastingCardTests(unittest.TestCase):
    def test_type_belongs_to_the_selected_role(self):
        body = "Сериал.\nГлавная роль: мужчина 35–40 лет\nМассовка: женщины 30–35 лет"
        self.assertEqual(card_heading(body, body.splitlines()[-1]), "🎬 Сериал · Массовка")

    def test_commercial_can_have_a_lead(self):
        self.assertEqual(card_heading("Для рекламы ищем актрису на главную роль"),
                         "🎬 Рекламная съёмка · Главная роль")

    def test_number_of_shifts_does_not_imply_a_lead(self):
        self.assertEqual(card_heading("В сериал нужна актриса на роль врача, 6 смен"),
                         "🎬 Сериал · Актёрская роль")

    def test_episode_with_lines(self):
        self.assertEqual(card_heading("В сериал нужна официантка на одну сцену, две реплики"),
                         "🎬 Сериал · Эпизод с репликами")
