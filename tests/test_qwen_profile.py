"""Voice grouping preserves the words and avoids sentence-by-sentence restarts."""
import unittest
from yiban.audio.qwen_profile import GENERATION_OPTIONS, SEED, speech_segments


class QwenProfileTests(unittest.TestCase):
    def test_short_reply_keeps_one_generation_context(self):
        text = "根据资料，沙粒被制作成沙漏。它承载了同行的纪念。"
        self.assertEqual(speech_segments(text), [text])

    def test_long_text_is_complete_and_bounded(self):
        text = ("这是一段用于测试完整句意的内容，分段之后必须保持原来的顺序。" * 30)
        chunks = speech_segments(text)
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(0 < len(c) <= 120 for c in chunks))
        self.assertLess(len(chunks), 15)

    def test_boundary_does_not_cut_numbers_or_scores(self):
        text = "甲" * 117 + "1,234本，比分38:49。" + "乙" * 120
        chunks = speech_segments(text)
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(any('1,234' in c for c in chunks))
        self.assertTrue(any('38:49' in c for c in chunks))

    def test_generation_policy_reduces_sampling_variation(self):
        self.assertLess(GENERATION_OPTIONS['temperature'], .9)
        self.assertLess(GENERATION_OPTIONS['top_k'], 50)
        self.assertLess(GENERATION_OPTIONS['top_p'], 1)
        self.assertIsInstance(SEED, int)


if __name__ == '__main__':
    unittest.main()
