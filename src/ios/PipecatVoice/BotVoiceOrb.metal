#include <metal_stdlib>
using namespace metal;

namespace {
float orbHash(float3 p) {
    p = fract(p * 0.3183099 + float3(0.17, 0.31, 0.53));
    p *= 17.0;
    return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}

// Quintic interpolation keeps the density field and its motion continuous.
float orbNoise(float3 p) {
    float3 i = floor(p);
    float3 f = fract(p);
    f = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    return mix(mix(mix(orbHash(i), orbHash(i + float3(1, 0, 0)), f.x),
                   mix(orbHash(i + float3(0, 1, 0)), orbHash(i + float3(1, 1, 0)), f.x), f.y),
               mix(mix(orbHash(i + float3(0, 0, 1)), orbHash(i + float3(1, 0, 1)), f.x),
                   mix(orbHash(i + float3(0, 1, 1)), orbHash(i + float3(1, 1, 1)), f.x), f.y), f.z);
}

float orbTurbulence(float3 p) {
    return orbNoise(p) * 0.58
        + orbNoise(p.yzx * 2.03 + 7.1) * 0.28
        + orbNoise(p.zxy * 4.09 + 13.7) * 0.14;
}

float2 orbRotate(float2 p, float angle) {
    float s = sin(angle), c = cos(angle);
    return float2(c * p.x - s * p.y, s * p.x + c * p.y);
}

float3 orbTint(float3 p, float time) {
    float cyan = exp(-2.4 * dot(p.xy - float2(-0.38, 0.12), p.xy - float2(-0.38, 0.12)));
    float rose = exp(-2.8 * dot(p.xy - float2(0.38, -0.20), p.xy - float2(0.38, -0.20)));
    float violet = 0.38 + 0.15 * sin(p.z * 3.0 + time * 0.18);
    return (float3(0.08, 0.82, 1.0) * cyan
            + float3(1.0, 0.10, 0.44) * rose
            + float3(0.38, 0.20, 1.0) * violet) / (cyan + rose + violet);
}

float3 auraTint(float angle) {
    // Cyan, periwinkle, fuchsia and a small warm highlight travel around the rim.
    float3 color = 0.55 + 0.45 * cos(angle + float3(0.0, 2.1, 4.2));
    return mix(color, float3(0.34, 0.48, 1.0), 0.12);
}
}

/// A breathing aura of interwoven light, with translucent folds inside the ring.
/// SwiftUI supplies a continuous local clock and a smoothed bot-only envelope.
[[ stitchable ]] half4 botVoiceWisps(float2 position, float2 size, float time,
                                    float energy, float darkAppearance) {
    float2 uv = (position - size * 0.5) / (max(1.0, min(size.x, size.y)) * 0.5);
    float screenRadius = length(uv);
    float edge = 1.0 - smoothstep(0.90, 1.0, screenRadius);
    if (edge <= 0.0) { return half4(0); }
    float angle = atan2(uv.y, uv.x);
    float expansion = 0.81 + energy * 0.19;
    float2 volumeUV = uv / expansion;
    float radius = length(volumeUV);

    float3 haloTint = auraTint(angle - time * 0.35);
    float halo = exp(-pow((radius - 0.73) * 5.4, 2.0)) * edge
        * mix(0.12, 0.20, darkAppearance) * (0.55 + energy * 1.4);
    float3 output = haloTint * halo;
    float alpha = halo;

    const float sphereRadius = 0.79;
    if (radius < sphereRadius) {
        float depth = sqrt(max(0.0, sphereRadius * sphereRadius - radius * radius));
        const int steps = 40;
        float stepSize = depth * 2.0 / float(steps);
        float3 light = float3(0);
        float transmission = 1.0;
        float3 drift = float3(time * 0.16, -time * 0.19, time * 0.12);

        for (int index = 0; index < steps; ++index) {
            float3 p = float3(volumeUV, depth - (float(index) + 0.5) * stepSize);
            float envelope = 1.0 - smoothstep(0.50, sphereRadius, length(p));
            p.xz = orbRotate(p.xz, time * 0.23);
            p.xy = orbRotate(p.xy, time * 0.16 + sin(time * 0.29) * 0.24);
            float3 q = p + sin(p.yzx * 3.3 + drift) * (0.10 + energy * 0.16);
            float turbulence = orbTurbulence(q * 3.1 + drift);

            // Narrow luminous folds sit inside wider, almost transparent veils.
            float fold = q.y + sin(q.x * 3.0 + time * 0.24) * 0.30
                + sin(q.z * 3.7 - time * 0.19) * 0.21 + (turbulence - 0.5) * 0.72;
            float filament = fold + sin(q.x * 8.0 + q.z * 5.0 + turbulence * 6.0) * 0.045;
            float curl = q.z * 0.65 - q.x * 0.55 + sin(q.y * 3.4 + time * 0.22) * 0.22
                + (turbulence - 0.5) * 0.65;
            float veil = exp2(-fold * fold * 35.0);
            float strand = exp2(-filament * filament * 550.0);
            float crossing = exp2(-curl * curl * 160.0);
            float density = (veil * 0.18 + strand * 0.72 + crossing * 0.32 + 0.035)
                * envelope * (0.42 + turbulence * 0.75);
            float opacity = 1.0 - exp(-density * stepSize * 4.2);
            float3 tint = orbTint(p, time);
            tint = mix(tint, float3(0.78, 0.93, 1.0), strand * 0.35);
            light += transmission * tint * opacity * (0.95 + energy * 1.50);
            transmission *= 1.0 - opacity;
        }

        float mistBoundary = 1.0 - smoothstep(0.38, sphereRadius, radius);
        float litBoundary = 1.0 - smoothstep(0.62, sphereRadius, radius);
        float body = mix(mistBoundary, litBoundary, min(1.0, (1.0 - transmission) * 1.7));
        float3 shade = mix(float3(0.035, 0.045, 0.12), float3(0.065, 0.09, 0.19),
                           1.0 - smoothstep(-0.6, 0.6, volumeUV.y));
        float3 color = shade + (1.0 - exp(-light * 2.5));
        // A soft internal light replaces a shiny surface highlight and hard rim.
        float bloom = exp(-dot(volumeUV + float2(0.08, 0.02), volumeUV + float2(0.08, 0.02)) * 8.0);
        color += float3(0.16, 0.24, 0.33) * bloom * (1.0 - transmission) * 0.3;
        color = min(color, float3(1.0));
        output = color * body + output * (1.0 - body);
        alpha = body + alpha * (1.0 - body);
    }

    // Soft ribbons peel away from one another on louder syllables. All radial
    // motion is driven by audio energy; time only carries the flowing light.
    float auraRadius = 0.64 + energy * 0.135;
    float ripple = 0.007 + energy * 0.032;
    float3 auraLight = float3(0);
    float auraDensity = 0.0;
    for (int ribbon = 0; ribbon < 4; ++ribbon) {
        float offset = float(ribbon) * 1.5707963;
        float wave = sin(angle * 3.0 + time * 0.85 + offset) * 0.60
            + sin(angle * 5.0 - time * 0.63 + offset * 1.7) * 0.28
            + sin(angle * 2.0 - time * 0.41 + offset) * 0.30;
        float ring = auraRadius + wave * ripple
            + (float(ribbon) - 1.5) * (0.006 + energy * 0.010);
        float distance = screenRadius - ring;
        float width = 0.008 + energy * 0.008;
        float filament = exp(-pow(distance / width, 2.0));
        float veil = exp(-pow(distance / (width * 4.5), 2.0));
        float highlight = 0.55 + 0.45 * sin(angle * 2.0 + time * 0.9 + offset);
        float density = (filament * 0.55 + veil * 0.16)
            * (0.35 + energy * 0.85) * highlight;
        float3 tint = auraTint(angle - time * 0.35 + offset * 0.32);
        tint = mix(tint, float3(0.78, 0.94, 1.0), filament * highlight * energy * 0.48);
        auraLight += tint * density;
        auraDensity += density;
    }
    float auraAlpha = (1.0 - exp(-auraDensity * 2.8)) * edge;
    float3 auraColor = auraLight / max(0.0001, auraDensity);
    output = auraColor * auraAlpha + output * (1.0 - auraAlpha);
    alpha = auraAlpha + alpha * (1.0 - auraAlpha);
    return half4(half3(output), half(alpha));
}
