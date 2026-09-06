#ifndef PHONON_H
#define PHONON_H
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

typedef struct PhononModel PhononModel;
typedef struct PhononCancellation PhononCancellation;
// PCM is mono float32 at 24 kHz; the pointer is valid only during the callback.
typedef bool (*PhononAudioCallback)(const float *, size_t, void *);

PhononModel *phonon_load(const char *root, const char *voice, const char *key,
                        char *error, size_t error_capacity);
// Calls on a model, including free, must be serialized by the host.
void phonon_free(PhononModel *model);
PhononCancellation *phonon_cancellation_new(void);
void phonon_cancel(PhononCancellation *token);
// Free only after phonon_speak has returned.
void phonon_cancellation_free(PhononCancellation *token);
int32_t phonon_speak(const PhononModel *model, const char *text,
                     const PhononCancellation *token, PhononAudioCallback callback,
                     void *context, char *error, size_t error_capacity);
#endif
