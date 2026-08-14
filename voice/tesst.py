import pyttsx3

def speak(text):
    print("Entering speak()")

    engine = pyttsx3.init()

    print("Engine created")

    engine.setProperty("rate", 175)

    engine.say(text)

    print("Speaking...")

    engine.runAndWait()

    print("Finished")

    engine.stop()


speak("Hello, this is the first message.")
speak("This is the second message.")