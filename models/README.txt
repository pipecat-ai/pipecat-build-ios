Drop the supplied Gradium Phonon package into this folder before building.
Keep the folder name "phonon" and include the Rust inference source as well as
the model files. The app's native wrapper depends on models/phonon/Cargo.toml.

Expected layout:

models/
  README.txt
  phonon/
    Cargo.toml
    Cargo.lock
    src/
    model/
      model.q8.gguf
      config.json
      tokenizer.model
    voices/
      Marlowe.safetensors
      Freya.safetensors
      Archie.safetensors
      Freddie.safetensors
      Elodie-Rose.safetensors
      Garrett.safetensors
      Damon.safetensors
      Zoey.safetensors

Everything in models/ except this note is ignored by Git. Keep all supplied
voice files so that every option in the app's voice picker is available.

Enter your Gradium Phonon key in the app under Set up voice or Voice settings.
The app stores it in the device's Keychain; keys are not included in builds.
