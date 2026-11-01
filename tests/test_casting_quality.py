import unittest
from datetime import date
from app.casting_quality import actionable
from app.config import Profile
from app.matcher import match_post

PROFILE = Profile(city="Москва", gender="female", age=45, height_cm=170,
                  kids_ok=False, include_keywords=[],exclude_keywords=["набор закрыт"])
DATE = date(2026,10,31)

class CastingQualityTests(unittest.TestCase):
    def test_casting_training_examples(self):
        examples = [
            ("Кастинг в Москве. Ищем актрису 40-50 лет для съёмок.",True),
            ("На курсе актёрского мастерства вы узнаете, как пройти кастинг",False),
            ("Итоги кастинга: как прошли съемки вчера",False),
            ("Кастинг, приём заявок завершён. Роли утверждены",False),
            ("Кастинг в Москве. Нужна актриса, заявки до 10.10.2026",False),
            ("Набор актрис в кино в Москве, заявки до 10.12.2026",True),
            ("Ищу кастинг и работу актером в Москве",False),
            ("Новости российского кино: известные актеры",False),
        ]
        for text,expected in examples:
            with self.subTest(text=text):
                result=actionable(text,DATE)
                self.assertEqual(result[0],expected,result)

    def test_role_ranges_must_match_same_role(self):
        post="Кастинг в Москве.\nЖенщины 20-30 лет\nМужчины 40-50 лет"
        result=match_post(post, PROFILE)
        self.assertFalse(result.matched,result.reasons)
        profile = Profile(city="Москва",gender="female",age=25,height_cm=170,
                          kids_ok=False,include_keywords=[],exclude_keywords=[])
        self.assertTrue(match_post(post,profile).matched)

    def test_unknown_gender_role_not_rejected(self):
        post="Кастинг в Москве. Ищем людей 30-50 лет для съемок рекламы"
        self.assertTrue(match_post(post,PROFILE).matched)

if __name__=="__main__":
    unittest.main()
