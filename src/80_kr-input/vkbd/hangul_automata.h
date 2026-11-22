#ifndef HANGUL_AUTOMATA_H
#define HANGUL_AUTOMATA_H

#include <stdint.h>
#include <stdbool.h>

typedef struct {
    int cho;        /* -1 or 0..18 */
    int jung;       /* -1 or 0..20 */
    int jung_first; /* -1 or first vowel index if compound vowel */
    int jong;       /*  0 or 1..27 */
    int jong_first; /*  0 or first jong index if compound jong */
} hangul_state_t;

void hangul_init(hangul_state_t *st);
bool hangul_is_empty(const hangul_state_t *st);

/* Returns current composing codepoint (0 if empty, Compatibility Jamo or Syllable) */
uint32_t hangul_current_codepoint(const hangul_state_t *st);

/*
 * Feed a compatibility Jamo codepoint (U+3131 .. U+3163).
 * Returns number of committed codepoints (0, 1, or 2) written to committed_out[].
 */
int hangul_feed_jamo(hangul_state_t *st, uint32_t jamo, uint32_t committed_out[2]);

/*
 * Flush any composing character. Returns 1 if flushed into *committed_out, 0 if empty.
 */
int hangul_flush(hangul_state_t *st, uint32_t *committed_out);

/*
 * Backspace one step inside the composing syllable.
 * Returns true if consumed inside automata, false if automata was already empty.
 */
bool hangul_backspace(hangul_state_t *st);

#endif /* HANGUL_AUTOMATA_H */

