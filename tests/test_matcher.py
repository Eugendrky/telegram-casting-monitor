import unittest

from app.config import Profile
from app.matcher import match_post


def _profile(**kwargs) -> Profile:
    base = dict(
        city="Москва",
        gender="female",
        age=25,
        height_cm=170,
        kids_ok=False,
        include_keywords=[],
        exclude_keywords=["набор закрыт"],
    )
    base.update(kwargs)
    return Profile(**base)


class MatcherTests(unittest.TestCase):
    def test_matches_moscow_female_age(self) -> None:
        text = "Кастинг в Москве, ищем девушек 20-30 лет на съёмки рекламы, рост 165-175"
        result = match_post(text, _profile())
        self.assertTrue(result.matched, result.reasons)
        self.assertGreaterEqual(result.score, 5)

    def test_rejects_other_city(self) -> None:
        text = "Кастинг актёров в Казани, нужны девушки 20-30"
        result = match_post(text, _profile())
        self.assertFalse(result.matched)

    def test_rejects_male_only(self) -> None:
        text = "Кастинг в Москве, нужны парни 20-30 лет, мужской типаж"
        result = match_post(text, _profile())
        self.assertFalse(result.matched)

    def test_rejects_closed(self) -> None:
        text = "Кастинг в Москве, девушки 20-30. Набор закрыт"
        result = match_post(text, _profile())
        self.assertFalse(result.matched)

    def test_skips_non_casting(self) -> None:
        text = "Продам диван в Москве, самовывоз"
        result = match_post(text, _profile())
        self.assertFalse(result.matched)


if __name__ == "__main__":
    unittest.main()
