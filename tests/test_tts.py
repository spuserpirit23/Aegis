import unittest
from unittest.mock import Mock, patch

import voice.tts as tts


class TextToSpeechTests(unittest.TestCase):
    def test_speak_uses_fresh_engine_and_stops_each_time(self):
        first_engine = Mock()
        second_engine = Mock()
        fake_pyttsx3 = Mock()
        fake_pyttsx3.init.side_effect = [first_engine, second_engine]

        with patch.object(tts, "pyttsx3", fake_pyttsx3):
            tts.speak("first")
            tts.speak("second")

        self.assertEqual(fake_pyttsx3.init.call_count, 2)
        first_engine.say.assert_called_once_with("first")
        second_engine.say.assert_called_once_with("second")
        first_engine.runAndWait.assert_called_once_with()
        second_engine.runAndWait.assert_called_once_with()
        first_engine.stop.assert_called_once_with()
        second_engine.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
