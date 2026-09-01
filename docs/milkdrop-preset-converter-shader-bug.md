# milkdrop-preset-converter: chained addition mistranslated as invalid GLSL

**Package:** [`milkdrop-preset-converter`](https://github.com/jberg/milkdrop-preset-converter) (v0.1.2)
**Likely origin:** the HLSL→AST step via [`hlslparser-js`](https://github.com/jberg/hlslparser-js), or the GLSL codegen in `milkdrop-preset-converter` that walks that AST
**Impact:** high — hit ~67% of a random sample of ~10k real-world `.milk` presets (see "Scope" below)
**Severity:** in our case, feeding the bad output to WebGL hung the renderer rather than failing with a fast compile error

## The bug

When converting a MilkDrop preset's warp or composite shader (the `warp` /
`comp` HLSL code in a `.milk` file), a chained addition in the original
source — roughly `A + B + C` — comes out of the converter as:

```glsl
bvecN(A) && bvecN(B)
```

or, for a 3+-way chain:

```glsl
bvecN(A) && (bvecN(B) && bvecN(C))
```

This is invalid GLSL: `&&`/`||` only accept a scalar `bool` operand, never
`bvec2`/`bvec3`/`bvec4` (a vector of bool). The converter's own shader
preamble even defines a `bvecTernary0()` helper clearly meant for
component-wise boolean-vector operations — the buggy code path just never
routes through it here.

## Reproduction

Any `.milk` preset whose warp/comp shader source contains a numeric
addition of the shape `X + Y` (`Y` a vector-typed sub-expression, not
necessarily literal) is a candidate. A concrete, minimal-ish example from
the standard bundled MilkDrop default warp shader boilerplate (used
verbatim by a huge number of presets that never customize it):

Original MilkDrop HLSL (paraphrased from the stock template):
```c
ret = max(ret, tex2D(sampler_main, (uv-0.5)*(1-8*texsize.zw)+0.5).xyz);
```

`milkdrop-preset-converter` output (invalid GLSL):
```glsl
(ret = max(ret, (texture(sampler_main, vec2 ((bvec2 (((uv - vec2 (0.500000)) * vec2 ((float (1) - (float (8) * length((texsize).zw)))))) && bvec2 (0.500000))))).xyz));
```

The `... && bvec2(0.500000)` at the end is standing in for `... + 0.5`.

A second, real example (chained 3-way add) from
`presets-cream-of-the-crop/Dancer/Comet/EoS + Phat - chasers 11 sentinel C (Jelly V2).milk`:

```glsl
(ret = mix(ret, vec3 ((bvec3 ((ret - (vec3 (0.100000) * vec3 (...)))) && (bvec3 ((vec3 (0.100000) * vec3 (...))) && bvec3 ((vec3 (0.420000) * (texture(sampler_main, uv_y)).xyz)))))), vec3 (0.250000)));
```
— the nested `A && (B && C)` shape here matching a 3-term chained addition
(`ret - X + Y + Z`).

## Scope

Batch-testing `convertPreset()` against a random sample of 50 real presets
from a large community collection (after fixing an unrelated too-aggressive
timeout in the test harness — some presets are just legitimately CPU-heavy
to convert, not hung):

- 48/50 converted with **zero** occurrences of the bad `bvecN(...) &&/||`
  pattern in `warp`/`comp` after applying the workaround below
- 2/50 failed for unrelated reasons (one genuine HLSL syntax error the
  parser can't handle, one `"No function matching: assign"` codegen error)

An earlier, smaller sample *without* the workaround showed the bad pattern
in roughly two-thirds of presets — this is not an edge case, it affects the
majority of real-world content.

## Our workaround

Since we can't patch the converter's own (minified, npm-distributed)
codegen, we post-process its shader output in the consuming app instead:
`repairBadShaderPass()` / `repairBadShader()` in
[`src/web/main.js`](../src/web/main.js) (around line 58) scans the
generated `warp`/`comp` GLSL for `bvecN(A) && bvecN(B)` (recursing into
the 3+-way chained case where the right-hand side is a bare parenthesized
group instead of another `bvecN(...)`), and rewrites it to `(A) + (B)`,
iterating to a fixpoint (bounded at 10 passes). We keep a regex-based
rejection check (`badShaderPattern` in `loadPresetFile`) as a
belt-and-suspenders fallback in case some other invalid-GLSL variant of
this bug slips through the rewrite — better to refuse a load cleanly than
risk repeating the renderer hang.

This is a workaround, not a real fix: it pattern-matches the *symptom* in
generated text rather than correcting whatever AST-to-GLSL codegen step
in `milkdrop-preset-converter` (or the HLSL AST it gets from
`hlslparser-js`) is confusing an addition for a logical AND/OR. A proper
fix belongs upstream, ideally from whoever wrote that codegen step — filing
an issue (or PR, if the root cause in the actual source can be located) at
[jberg/milkdrop-preset-converter](https://github.com/jberg/milkdrop-preset-converter)
with this writeup is the next step if we want to fix this at the source
instead of carrying the workaround indefinitely.
