//! Streaming C ABI for the supplied Gradium Phonon implementation.
use std::ffi::{CStr, c_char, c_void};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};

pub struct PhononModel(gphonon::TtsModel);
pub struct PhononCancellation(AtomicBool);
type AudioCallback = unsafe extern "C" fn(*const f32, usize, *mut c_void) -> bool;

unsafe fn string(ptr: *const c_char) -> Result<String, String> {
    if ptr.is_null() {
        return Err("A required string is missing".into());
    }
    unsafe { CStr::from_ptr(ptr) }
        .to_str()
        .map(str::to_owned)
        .map_err(|e| e.to_string())
}

unsafe fn write_error(ptr: *mut c_char, capacity: usize, message: &str) {
    if !ptr.is_null() && capacity > 0 {
        let bytes = message.as_bytes();
        let n = bytes.len().min(capacity - 1);
        unsafe {
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), ptr.cast(), n);
            *ptr.add(n) = 0;
        }
    }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn phonon_load(
    root: *const c_char,
    voice: *const c_char,
    key: *const c_char,
    error: *mut c_char,
    capacity: usize,
) -> *mut PhononModel {
    let result = catch_unwind(AssertUnwindSafe(|| -> Result<PhononModel, String> {
        let root = PathBuf::from(unsafe { string(root)? });
        let voice = unsafe { string(voice)? };
        // Voice selection is a file name, never a caller-supplied path.
        if voice.contains('/') || voice.contains('\\') || voice.contains("..") {
            return Err("Invalid voice name".into());
        }
        let model = gphonon::TtsModel::load(gphonon::TtsPaths {
            model: root.join("model/model.q8.gguf"),
            config: root.join("model/config.json"),
            tokenizer: root.join("model/tokenizer.model"),
            voice_paths: vec![root.join("voices").join(format!("{voice}.safetensors"))],
            api_key: unsafe { string(key)? },
        })
        .map_err(|e| format!("{e:#}"))?;
        Ok(PhononModel(model))
    }));
    match result {
        Ok(Ok(model)) => Box::into_raw(Box::new(model)),
        other => {
            let message = match other {
                Ok(Err(e)) => e,
                _ => "Phonon panicked while loading the model".into(),
            };
            unsafe {
                write_error(error, capacity, &message);
            }
            std::ptr::null_mut()
        }
    }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn phonon_free(model: *mut PhononModel) {
    if !model.is_null() {
        drop(unsafe { Box::from_raw(model) });
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn phonon_cancellation_new() -> *mut PhononCancellation {
    Box::into_raw(Box::new(PhononCancellation(AtomicBool::new(false))))
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn phonon_cancel(token: *mut PhononCancellation) {
    if let Some(token) = unsafe { token.as_ref() } {
        token.0.store(true, Ordering::Release);
    }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn phonon_cancellation_free(token: *mut PhononCancellation) {
    if !token.is_null() {
        drop(unsafe { Box::from_raw(token) });
    }
}

// Returns 0 on success, 1 on cancellation, and -1 on error. Panics never unwind
// into Swift. Dropping the receiver stops the decoder at its next PCM chunk.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn phonon_speak(
    model: *const PhononModel,
    text: *const c_char,
    token: *const PhononCancellation,
    callback: AudioCallback,
    context: *mut c_void,
    error: *mut c_char,
    capacity: usize,
) -> i32 {
    let result = catch_unwind(AssertUnwindSafe(|| -> Result<i32, String> {
        let model = unsafe { model.as_ref() }.ok_or("Model is not loaded")?;
        let token = unsafe { token.as_ref() }.ok_or("Cancellation token is missing")?;
        let text = unsafe { string(text)? };
        if text.chars().count() > 600 {
            return Err("Speech text exceeds 600 characters".into());
        }
        if token.0.load(Ordering::Acquire) {
            return Ok(1);
        }
        let (tx, rx) = std::sync::mpsc::channel::<Vec<f32>>();
        let context = context as usize;
        std::thread::scope(|scope| {
            let collector = scope.spawn(move || {
                while let Ok(pcm) = rx.recv() {
                    if token.0.load(Ordering::Acquire)
                        || !unsafe { callback(pcm.as_ptr(), pcm.len(), context as *mut c_void) }
                    {
                        token.0.store(true, Ordering::Release);
                        break;
                    }
                }
            });
            let result = model.0.run(&text, 0, 42, 0.4, gphonon::Lang::En, tx);
            collector
                .join()
                .map_err(|_| "Audio callback failed".to_owned())?;
            if token.0.load(Ordering::Acquire) {
                return Ok(1);
            }
            result.map_err(|e| format!("{e:#}"))?;
            Ok(0)
        })
    }));
    match result {
        Ok(Ok(status)) => status,
        other => {
            let message = match other {
                Ok(Err(e)) => e,
                _ => "Phonon panicked during speech generation".into(),
            };
            unsafe {
                write_error(error, capacity, &message);
            }
            -1
        }
    }
}
