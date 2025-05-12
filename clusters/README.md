# Holocron Knowledge Cluster Analysis

## Overview

This analysis contains 76 clusters derived from BERT/E5 vector embeddings using HDBSCAN clustering.

## Top Clusters by Size

| Cluster ID | Size | Top Terms | Suggested Application |
|-----------|------|-----------|------------------------|
| 67 | 609 | cantina, information, with, that, location | Location/Planet Information |
| 34 | 527 | that, tano, with, skywalker, their | Character Information Retrieval |
| 74 | 518 | jabba, information, that, with, band | Character Information Retrieval |
| 39 | 256 | droid, droids, were, series, with | Historical Events |
| 15 | 245 | wars, star, lego, canon, appearances | General Knowledge Domain |
| 12 | 240 | star, wars, theme, episode, anakin | Character Information Retrieval |
| 28 | 210 | star, wars, galaxy, edge, disney | Historical Events |
| 68 | 172 | star, republic, wars, appearances, first | Mixed Content Cluster |
| 53 | 152 | star, wars, behind, scenes, mentioned | Mixed Content Cluster |
| 58 | 135 | organa, that, leia, with, solo | Character Information Retrieval |

## Noise Points

**Size:** 2451 vectors

**Top Terms:** star, that, wars, with, first, from, jedi, were

These points represent outliers that didn't fit well into any cluster. They may contain unique or specialized knowledge that could be valuable for specific queries.


## Detailed Cluster Analysis

### Cluster 67

**Size:** 609 vectors

**Key Terms:** cantina, information, with, that, location, source, general, located, planet, were, city, during, from, galactic, republic

**Title Patterns:** cantina, unidentified, the, tavern, bar, lounge, spaceport, and, city, rest

**Application:** Location/Planet Information - This cluster focuses on Star Wars locations and could enhance spatial context in responses.

#### Representative Documents

**Document 1:** Rim's Edge

```
# Rim's Edge

Rim's Edge cantina


General information

Location
An outpost, Freerock, Expansion Region


[Source]

Rim's Edge was a cantina located in the pit of an abandoned monastery, which became ...
```

**Document 2:** Wanton Wellspring

```
# Wanton Wellspring - Description[]

The Wanton Wellspring was a cantina located on the arid planet Rajtiri. Located in the city center of the city of Jibuto, the Wanton Wellspring was a hole-in-the-w...
```

**Document 3:** The Alcazar

```
# The Alcazar - Main area[]

The Alcazar was a cantina located in Myrra, the capital of Akiva, that was popular with the locals. The main area had an elevated stage for musicians to play and a long bl...
```

### Cluster 34

**Size:** 527 vectors

**Key Terms:** that, tano, with, skywalker, their, from, they, were, them, jedi, clone, after, kenobi, would, when

**Title Patterns:** rex, ahsoka, tano, gregor, cody, clone, captain, underground, disambiguation, platoon

**Application:** Character Information Retrieval - This cluster contains detailed character information and could be used for answering character-specific questions.

#### Representative Documents

**Document 1:** Clone Underground

```
# Clone Underground - Members[]

The Clone Underground movement consisted of multiple clone members. Within the movement's ranks were former Clone Commander Rex, who served as the leader of the organi...
```

**Document 2:** Clone Underground

```
# Clone Underground

Clone Underground


General information

Military unit type
Underground resistance network



Organizational information

Commanding officer(s)
Clone Captain Rex (retired)Corporal...
```

**Document 3:** Clone Underground

```
# Clone Underground - Organization[]

In 19 BBY, following the execution of Order 66, the Clone Underground was established by Rex as a resistance network. The goals of the Clone Underground included ...
```

### Cluster 74

**Size:** 518 vectors

**Key Terms:** jabba, information, that, with, band, song, they, source, their, during, from, music, after, were, would

**Title Patterns:** song, the, jabba, unidentified, desilijic, tiure, rebo, figrin, human, wane

**Application:** Character Information Retrieval - This cluster contains detailed character information and could be used for answering character-specific questions.

#### Representative Documents

**Document 1:** Bonbrak Protection Society

```
# Bonbrak Protection Society

The Bonbrak Protection Society were a group tasked with protecting Bonbraks, a sentient species that had been classified as a protected species by the Galactic Republic. ...
```

**Document 2:** Alisia (Human)

```
# Alisia (Human) - Biography[]

A Human female by the name of Alisia participated in a band during the time of the Galactic Civil War along with two other musicians, a Human named Mako, and a Zabrak n...
```

**Document 3:** Alisia (Human)

```
# Alisia (Human) - Equipment[]

As a band member at the cantinas of both Tansarii Point Station and Nova Orion Station, Alisia wore revealing clothing, including a yellow and white low cut top, yellow...
```

### Cluster 39

**Size:** 256 vectors

**Key Terms:** droid, droids, were, series, with, information, model, their, tactical, that, source, they, used, color, class

**Title Patterns:** droid, series, and, unidentified, tactical, corellia, model, flying, attack, military

**Application:** Historical Events - This cluster focuses on key events in Star Wars history, useful for timeline and historical questions.

#### Representative Documents

**Document 1:** Directional droid

```
# Directional droid

Directional droid


Production information

Model
Directional droid



Technical specifications

Plating color
Gray



Chronological and political information

Affiliation
Galacti...
```

**Document 2:** Unidentified T-series tactical droid (Corellia)

```
# Unidentified T-series tactical droid (Corellia)

Unidentified T-series tactical droid


Production information

Date destroyed
19 BBY, Corellia


Manufacturer
Baktoid Combat Automata


Model
T-serie...
```

**Document 3:** Unidentified T-series tactical droid (Corellia)

```
# Unidentified T-series tactical droid (Corellia) - Decommissioned droids[]

A T-series military strategic analysis and tactics droid produced by Baktoid Combat Automata served the droid army of the C...
```

### Cluster 15

**Size:** 245 vectors

**Key Terms:** wars, star, lego, canon, appearances, adventures, freemaker, only, saga, skywalker, tales, mentioned, original, flashback, disney

**Title Patterns:** droid, the, unit, imperial, milk, and, series, commando, captain, figrin

**Application:** General Knowledge Domain - Large cluster with diverse content, could serve as a general knowledge source for broad queries.

#### Representative Documents

**Document 1:** Weapon

```
# Weapon - Non-canon appearances[]

LEGO Star Wars: The Complete Saga

...
```

**Document 2:** Weapon

```
# Weapon - Non-canon appearances[]

LEGO Star Wars: The Complete Saga

...
```

**Document 3:** Pilot droid

```
# Pilot droid - Non-canon appearances[]

LEGO Star Wars: The Freemaker Adventures — "The Test"
 LEGO Star Wars: The Freemaker Adventures — "The Maker of Zoh" (Head and legs only)
 LEGO Star Wars: All-...
```

