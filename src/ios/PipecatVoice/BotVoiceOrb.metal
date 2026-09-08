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
    // A close family of blue and periwinkle, with pearl light in the folds.
    float blend = 0.5 + 0.5 * sin(p.x * 2.1 + p.y * 1.5 + p.z * 1.8 + time * 0.10);
    return mix(float3(0.20, 0.46, 0.88), float3(0.46, 0.40, 0.78), blend);
}
}

/// A breathing volume of translucent silk, feathering into loose outer wisps.
/// SwiftUI supplies a continuous local clock and a smoothed bot-only envelope.
[[ stitchable ]] half4 botVoiceWisps(float2 position, float2 size, float time,
                                    float energy, float darkAppearance) {
    float2 uv = (position - size * 0.5) / (max(1.0, min(size.x, size.y)) * 0.5);
    float screenRadius = length(uv);
    float edge = 1.0 - smoothstep(0.90, 1.0, screenRadius);
    if (edge <= 0.0) { return half4(0); }
    float angle = atan2(uv.y, uv.x);
    float expansion = 0.81 + energy * 0.19;
    // Two broad lobes flex the whole volume with each syllable. The outer wisps
    // share this deformation so their edges feel attached to the moving cloud.
    float flex = energy * (sin(angle * 2.0 + time * 0.27) * 0.13
        + sin(angle * 3.0 - time * 0.19) * 0.055);
    float2 volumeUV = uv / (expansion * (1.0 + flex));
    float radius = length(volumeUV);

    float3 haloTint = float3(0.40, 0.49, 0.85);
    float halo = exp(-pow((radius - 0.48) * 3.2, 2.0)) * edge
        * mix(0.10, 0.15, darkAppearance) * (0.60 + energy * 1.2);
    float3 output = haloTint * halo;
    float alpha = halo;

    const float sphereRadius = 0.79;
    if (radius < sphereRadius) {
        float depth = sqrt(max(0.0, sphereRadius * sphereRadius - radius * radius));
        const int steps = 48;
        float stepSize = depth * 2.0 / float(steps);
        float3 light = float3(0);
        float transmission = 1.0;
        float3 drift = float3(time * 0.09, -time * 0.11, time * 0.07);

        for (int index = 0; index < steps; ++index) {
            float3 p = float3(volumeUV, depth - (float(index) + 0.5) * stepSize);
            float envelope = 1.0 - smoothstep(0.46, sphereRadius, length(p));
            p.xz = orbRotate(p.xz, time * 0.16 + p.y * 0.35);
            p.xy = orbRotate(p.xy, time * 0.11 + sin(time * 0.19) * 0.24);
            float3 q = p + sin(p.yzx * 3.0 + drift) * (0.11 + energy * 0.09);
            float turbulence = orbTurbulence(q * 2.7 + drift * 0.8);

            // Narrow luminous folds sit inside wider, almost transparent veils.
            float fold = q.y + sin(q.x * 3.0 + time * 0.16) * 0.30
                + sin(q.z * 3.5 - time * 0.13) * 0.21 + (turbulence - 0.5) * 0.58;
            float filament = fold + sin(q.x * 6.0 + q.z * 4.0 + turbulence * 4.0) * 0.033;
            float curl = q.z * 0.65 - q.x * 0.5 + sin(q.y * 3.2 + time * 0.14) * 0.22
                + (turbulence - 0.5) * 0.48;
            float veil = exp2(-fold * fold * 55.0);
            float strand = exp2(-filament * filament * 650.0);
            float crossing = exp2(-curl * curl * 200.0);
            float fibers = pow(0.5 + 0.5 * cos(fold * 65.0 + q.z * 3.0), 8.0) * veil;
            float density = (veil * 0.10 + strand * 0.58 + crossing * 0.30 + fibers * 0.16 + 0.005)
                * envelope * (0.60 + turbulence * 0.40);
            float opacity = 1.0 - exp(-density * stepSize * 4.2);
            float3 tint = orbTint(p, time);
            tint = mix(tint, float3(0.78, 0.88, 1.0), strand * 0.45);
            light += transmission * tint * opacity * (0.85 + energy * 1.15);
            transmission *= 1.0 - opacity;
        }

        // Density defines the silhouette, leaving air between the folded veils.
        float body = 1.0 - pow(transmission, 2.2);
        float3 color = float3(0.06, 0.15, 0.34) + (1.0 - exp(-light * 3.0));
        color = min(color, float3(1.0));
        output = color * body + output * (1.0 - body);
        alpha = body + alpha * (1.0 - body);
    }

    // Loose, tapered curls dissolve into the volume. Each is only a partial arc,
    // so the aura has an airy circular silhouette without a continuous outline.
    float auraRadius = 0.56 + energy * 0.13;
    float3 auraLight = float3(0);
    float auraDensity = 0.0;
    for (int wisp = 0; wisp < 3; ++wisp) {
        float offset = float(wisp) * 2.0943951;
        float travel = angle - time * 0.18 + offset;
        float taper = pow(0.5 + 0.5 * cos(travel), 3.0);
        float bend = sin(angle * 2.0 + time * 0.16 + offset) * (0.020 + energy * 0.065)
            + sin(angle * 3.0 - time * 0.12 + offset) * (0.01 + energy * 0.025);
        float contour = auraRadius * (1.0 + flex) + bend
            - float(wisp) * (0.025 + energy * 0.045);
        float distance = screenRadius - contour;
        float width = 0.009 + taper * 0.012 + energy * 0.007;
        float filament = exp(-pow(distance / width, 2.0));
        float veil = exp(-pow(distance / (width * 3.8), 2.0));
        float density = (filament * 0.70 + veil * 0.22)
            * (0.35 + energy * 0.95) * taper;
        float3 tint = mix(float3(0.18, 0.40, 0.82), float3(0.46, 0.48, 0.84), float(wisp) * 0.35);
        tint = mix(tint, float3(0.78, 0.87, 1.0), filament * taper * 0.50);
        auraLight += tint * density;
        auraDensity += density;
    }
    float auraAlpha = (1.0 - exp(-auraDensity * 2.0)) * edge;
    float3 auraColor = auraLight / max(0.0001, auraDensity);
    output = auraColor * auraAlpha + output * (1.0 - auraAlpha);
    alpha = auraAlpha + alpha * (1.0 - auraAlpha);
    return half4(half3(output), half(alpha));
}
