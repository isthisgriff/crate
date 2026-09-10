# Crate

> **An intelligent, local-first sample library manager for music producers.**

Crate is a desktop application designed to make large music sample libraries easier to **organize, search, discover, and use**.

Instead of relying entirely on filenames and folder structures, Crate analyzes the actual audio contained within a sample library and progressively builds an understanding of each sound.

The long-term goal is simple:

> **Describe the sound you want. Find it in your own library.**

---

## Why Crate?

Modern music producers can accumulate tens or hundreds of thousands of samples from:

* Sample packs
* Splice
* Downloads
* DAW projects
* Friends and collaborators
* Free sample libraries
* Years of accumulated files

Unfortunately, sample libraries often end up looking like this:

```text
Samples/
├── Pack_01/
│   ├── WAV/
│   │   ├── DRM_094.wav
│   │   └── Audio_338.wav
│
├── New_Drums/
│   ├── Kick_Final_2.wav
│   └── Kick_Final_3.wav
│
└── Random/
    ├── 808_new.wav
    └── FX_12_FINAL.wav
```

The problem isn't finding *a* sample.

The problem is finding the **right** sample.

A traditional file browser can answer:

> "Where is `Kick_Final_2.wav`?"

Crate aims to eventually answer:

> "Find me a dark, aggressive, distorted kick that would fit this track."

Or:

> "Find sounds similar to this sample."

Or:

> "Find samples from my library that fit the vibe of this reference track."

---

# Core Concept

Crate progressively transforms a raw sample library into an intelligent, searchable database.

```text
                  Sample Library
                        │
                        ▼
                ┌───────────────┐
                │ Library Scan  │
                └───────┬───────┘
                        │
                        ▼
                ┌───────────────┐
                │ Audio Metadata│
                └───────┬───────┘
                        │
                        ▼
                ┌───────────────┐
                │ Audio Analysis│
                └───────┬───────┘
                        │
                        ▼
                ┌───────────────┐
                │ AI / ML Layer │
                └───────┬───────┘
                        │
                        ▼
                ┌───────────────┐
                │ Search Engine │
                └───────┬───────┘
                        │
                        ▼
                  Useful Results
```

The system begins with simple deterministic information and progressively introduces more sophisticated audio intelligence.

---

# Features

## Current

> Development is currently focused on the foundational library infrastructure.

* [ ] Sample library indexing
* [ ] Persistent sample database
* [ ] Incremental library scanning
* [ ] Sample browser
* [ ] Audio preview/playback
* [ ] Waveform visualization
* [ ] Filename search
* [ ] Favorites and collections

## Planned

### Audio Analysis

* [ ] Duration and audio-format analysis
* [ ] Sample rate/channel detection
* [ ] RMS and peak analysis
* [ ] Loudness/LUFS analysis
* [ ] Spectral analysis
* [ ] BPM detection
* [ ] Key detection
* [ ] Onset/beat analysis

### Intelligent Organization

* [ ] Automatic sample categorization
* [ ] Filename/folder metadata extraction
* [ ] AI-generated tags
* [ ] User-defined tags
* [ ] Duplicate detection
* [ ] Near-duplicate detection

### AI Search

* [ ] Traditional audio-feature similarity search
* [ ] Neural audio embeddings
* [ ] Vector similarity search
* [ ] Natural-language sample search
* [ ] Search by example
* [ ] Hybrid keyword + semantic search
* [ ] Personalized result ranking

### Advanced Discovery

* [ ] Reference-track analysis
* [ ] Reference-track sample recommendations
* [ ] Context-aware sample recommendations
* [ ] DAW drag-and-drop integration
* [ ] Smart collections
* [ ] Producer-oriented workflows

---

# How It Will Work

Crate is designed around several layers.

```text
┌──────────────────────────────────────────────┐
│                 Desktop UI                  │
│                                              │
│ Search · Browser · Waveforms · Player        │
└───────────────────────┬──────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────┐
│               Application Core               │
│                                              │
│ Library Manager · Search · Jobs · Collections│
└───────────┬──────────────┬─────────────┬─────┘
            │              │             │
            ▼              ▼             ▼
      ┌──────────┐   ┌──────────┐   ┌──────────┐
      │  Audio   │   │ AI / ML  │   │ Database │
      │ Analysis │   │  Engine  │   │  SQLite  │
      └──────────┘   └──────────┘   └──────────┘
```

These components are intentionally separated so that the application can evolve without requiring the entire system to be rewritten.

---

# Sample Processing Pipeline

When Crate encounters a new sample, it will progressively build information about it.

```text
sample.wav
    │
    ├── Filesystem metadata
    │      ├── Filename
    │      ├── Path
    │      └── File size
    │
    ├── Audio metadata
    │      ├── Duration
    │      ├── Sample rate
    │      └── Channels
    │
    ├── Signal analysis
    │      ├── RMS
    │      ├── LUFS
    │      ├── Spectral features
    │      └── Temporal features
    │
    ├── Musical analysis
    │      ├── BPM
    │      └── Key
    │
    └── AI analysis
           ├── Classification
           ├── Tags
           └── Embedding
```

Not every sample will have meaningful values for every category.

For example, a kick drum may not have a meaningful BPM, while an 8-bar melodic loop likely will.

Crate should represent uncertainty rather than inventing metadata.

---

# Semantic Search

One of Crate's primary long-term goals is natural-language search.

A query such as:

```text
dark aggressive distorted kick
```

will eventually be converted into a representation that can be compared against representations of the audio in the user's library.

Conceptually:

```text
             "dark aggressive kick"
                       │
                       ▼
                 Text Encoder
                       │
                       ▼
                Text Embedding
                       │
                       ▼
                Vector Search
                       ▲
                       │
               Audio Embeddings
                       ▲
                       │
                 Sample Audio
```

This allows Crate to search based on **what a sound is like**, rather than merely what its filename happens to be.

---

# Similarity Search

Crate will also support searching by example.

A user can select a sample they already like:

```text
        ┌────────────────────┐
        │   kick_example.wav  │
        └─────────┬──────────┘
                  │
                  ▼
             Audio Model
                  │
                  ▼
            Audio Embedding
                  │
                  ▼
            Similarity Search
                  │
         ┌────────┼────────┐
         ▼        ▼        ▼
      Kick 01   Kick 27   Kick 93
```

This provides a way to discover sounds that may have completely unrelated filenames or folder locations.

---

# Local-First Design

Crate is designed primarily as a **local application**.

The user's sample library should remain on their machine whenever possible.

The architecture therefore favors:

* Local filesystem access
* Local SQLite database
* Local audio processing
* Local ML inference where practical
* Embedded vector search

Cloud services may eventually be supported as optional functionality, but Crate should not fundamentally depend on a cloud backend.

---

# Technology

The technology stack is intentionally allowed to evolve as development progresses.

The initial implementation is expected to favor a lightweight Python-based stack because development speed and experimentation are more important during the early phases.

Potential technologies include:

| Component       | Initial Direction          |
| --------------- | -------------------------- |
| Language        | Python                     |
| Desktop UI      | PySide6 / Qt               |
| Database        | SQLite                     |
| Audio analysis  | Python audio/DSP libraries |
| ML              | PyTorch                    |
| Vector search   | Embedded/local solution    |
| Testing         | pytest                     |
| Version control | Git                        |

Performance-sensitive components may eventually be migrated to C++ or Rust if profiling demonstrates that this is necessary.

The project deliberately avoids introducing additional infrastructure without a concrete requirement.

---

# Development Roadmap

Crate is being developed incrementally.

Each phase has a concrete endpoint and should result in a usable improvement to the application.

---

## Phase 0 — Project Foundation

**Goal:** Establish a clean, reproducible development environment and project architecture.

* [ ] Repository setup
* [ ] Project structure
* [ ] Dependency management
* [ ] Formatting/linting
* [ ] Testing infrastructure
* [ ] Initial documentation
* [ ] Initial domain model
* [ ] Configuration system

**Endpoint:**

A fresh checkout can be installed, executed, and tested successfully.

---

## Phase 1 — Sample Library Scanner

**Goal:** Crate can reliably inventory a user's sample library.

* [ ] Directory selection
* [ ] Recursive filesystem scanning
* [ ] Audio file detection
* [ ] Basic filesystem metadata extraction
* [ ] SQLite persistence
* [ ] Error handling
* [ ] Incremental indexing
* [ ] Detection of new/modified/deleted files
* [ ] Scan progress reporting

**Endpoint:**

Point Crate at a real sample library and receive a reliable persistent inventory without unnecessarily reprocessing unchanged files.

---

## Phase 2 — Sample Browser & Playback

**Goal:** Make Crate useful as a basic sample browser.

* [ ] Sample browser
* [ ] Filename search
* [ ] Metadata display
* [ ] Audio preview
* [ ] Play/pause/stop
* [ ] Seeking
* [ ] Waveform display
* [ ] Favorites
* [ ] Sorting/filtering

**Endpoint:**

Crate becomes a genuinely useful alternative to manually browsing sample folders.

---

## Phase 3 — Audio Analysis

**Goal:** Understand the actual properties of each sample.

* [ ] Duration
* [ ] Sample rate
* [ ] Channel information
* [ ] Peak amplitude
* [ ] RMS
* [ ] LUFS
* [ ] Spectral features
* [ ] MFCCs
* [ ] BPM detection
* [ ] Key detection
* [ ] Background processing
* [ ] Analysis job queue
* [ ] Failure/retry handling

**Endpoint:**

Crate can search/filter samples using actual audio characteristics rather than only filenames.

---

## Phase 4 — Intelligent Categorization

**Goal:** Reduce dependence on manually organized folders and filenames.

* [ ] Filename-based tagging
* [ ] Folder-based tagging
* [ ] Automatic sample categories
* [ ] ML-based classification
* [ ] Confidence scores
* [ ] User metadata overrides

**Endpoint:**

Crate can automatically provide useful categories and tags for a large sample library.

---

## Phase 5 — Audio Similarity

**Goal:** Find samples that sound similar using extracted audio characteristics.

* [ ] Feature-vector representation
* [ ] Feature normalization
* [ ] Similarity scoring
* [ ] Nearest-neighbor search
* [ ] Similar-sample UI
* [ ] Adjustable similarity weighting

**Endpoint:**

Select a sample and receive a useful ranked list of similar sounds.

---

## Phase 6 — Neural Audio Embeddings

**Goal:** Improve similarity search using learned audio representations.

* [ ] Evaluate pretrained audio models
* [ ] Select appropriate embedding model
* [ ] Local inference
* [ ] Embedding generation
* [ ] Embedding caching
* [ ] Vector index
* [ ] Similarity benchmarking
* [ ] Compare against traditional audio features

**Endpoint:**

Neural similarity provides demonstrably better retrieval than the traditional feature-based baseline.

---

## Phase 7 — Natural-Language Search

**Goal:** Search the sample library using descriptions of desired sounds.

Examples:

```text
"dark atmospheric synth"

"aggressive distorted kick"

"bright plucky melody"

"futuristic metallic percussion"

"soft ambient texture"
```

* [ ] Text/audio embedding strategy
* [ ] Semantic search
* [ ] Query processing
* [ ] Hybrid keyword + vector search
* [ ] Metadata filtering
* [ ] Result ranking
* [ ] Search UI

**Endpoint:**

A producer can describe the sound they want and retrieve useful samples from their own library.

---

## Phase 8 — AI Metadata

**Goal:** Generate useful metadata for poorly organized samples.

* [ ] AI-generated tags
* [ ] AI-generated descriptions
* [ ] Confidence scores
* [ ] Model/version tracking
* [ ] User overrides
* [ ] Batch processing

**Endpoint:**

Even samples with meaningless filenames can become meaningfully searchable.

---

## Phase 9 — Search by Example

**Goal:** Search using a sample as the query.

* [ ] Drag/drop reference sample
* [ ] Embedding extraction
* [ ] Similarity retrieval
* [ ] Similarity controls
* [ ] Improved ranking

**Endpoint:**

A producer can provide a sound they already like and discover similar sounds throughout their library.

---

## Phase 10 — Reference Track Matching

**Goal:** Recommend samples based on the characteristics of an entire track.

* [ ] Reference-track ingestion
* [ ] BPM analysis
* [ ] Key analysis
* [ ] Energy analysis
* [ ] Spectral analysis
* [ ] Semantic/audio embedding analysis
* [ ] Sample recommendation
* [ ] Context-aware ranking

**Endpoint:**

Given a reference track, Crate can recommend relevant samples from the user's own library.

---

# Design Principles

### Useful before impressive

Crate should solve real workflow problems before accumulating advanced technology.

### Incremental complexity

The system should progress from:

```text
Deterministic metadata
        ↓
Audio analysis
        ↓
Feature similarity
        ↓
Neural embeddings
        ↓
Semantic search
        ↓
Personalized recommendations
```

### Local-first

User files should remain private and local whenever practical.

### Responsive

Large sample libraries should not make the application unusable.

Expensive operations should run asynchronously and be cached.

### Resilient

A corrupt or unsupported file should never bring down an entire library scan.

### Extensible

The architecture should allow new analysis methods, ML models, search strategies, and metadata types to be introduced without rewriting the core application.

### Producer-focused

Every major feature should ultimately answer the question:

> **Does this help me find or use the sound I want faster?**

---

# Current Development Status

**Current Phase:** Phase 0 / Phase 1

Crate is currently in early development.

The immediate objective is to establish the foundation and build the first working library scanner.

The first major milestone is:

> **Point Crate at my real sample library and receive a reliable, persistent inventory of its audio files.**

From there, development will proceed incrementally toward intelligent audio analysis, similarity search, and semantic discovery.

---

# Long-Term Vision

The ultimate goal is for Crate to become an intelligent layer between a producer and their sample library.

Instead of remembering:

```text
Which pack had that sound?
What was the filename?
Where did I download it?
Was it in my drums folder?
```

the workflow becomes:

```text
"I need a dark, punchy 808."

             ↓

          Crate

             ↓

Relevant sounds from
my entire library
```

The software should eventually become something that **disappears into the creative workflow**.

Open it.

Find the sound.

Make music.

---

# Project Status

This project is actively being developed.

The roadmap above represents the intended development direction, but later phases may change based on actual usage, technical findings, and experimentation.

The goal is not to build every feature listed here.

The goal is to build the **best possible tool for actually finding sounds in a real-world sample library**.
