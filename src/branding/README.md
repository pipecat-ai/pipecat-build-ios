# Pipecat artwork

The app uses the official [Pipecat wordmark](https://github.com/pipecat-ai/docs/blob/main/logo/light.svg).
`PipecatWordmark.svg` is the original artwork. `PipecatMark.svg` contains its five
cat-symbol paths, with the original 40 × 23 proportions.

The vector images live in `../ios/PipecatVoice/Assets.xcassets/` and render as
templates in SwiftUI. The header uses the wordmark; the voice indicator and
assistant transcript avatar use the cat symbol.

`app-icon.svg` and `app-icon-dark.svg` place the same symbol on an opaque
1024 × 1024 canvas, using Pipecat's neutral website palette: charcoal `#18181B`
and off-white `#FAFAFA`. Their PNG exports are in `AppIcon.appiconset`. iOS applies
the icon mask. The PNGs are checked in, so building the app requires no image
conversion tools. To revise the icons, edit these SVGs and export opaque PNGs at
1024 × 1024 (the existing exports were rendered with Sharp).
