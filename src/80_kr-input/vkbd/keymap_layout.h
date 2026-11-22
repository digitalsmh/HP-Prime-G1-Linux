#ifndef KEYMAP_LAYOUT_H
#define KEYMAP_LAYOUT_H

#include <stdint.h>

typedef enum {
    ACT_NONE = 0,
    ACT_CHAR,       /* ASCII / Symbol codepoint */
    ACT_JAMO,       /* Korean 2-bulsik Jamo codepoint (U+3131..U+3163) */
    ACT_KEYSYM,     /* Direct X11 KeySym (BackSpace, Return, Escape, Tab, Arrows) */
    ACT_SHIFT,      /* Oneshot Shift toggle */
    ACT_LANG,       /* Toggle EN <-> KR */
    ACT_SYM,        /* Toggle 123 Symbol mode */
    ACT_CTRL        /* Oneshot sticky Control_L */
} key_action_t;

typedef struct {
    uint8_t col;       /* 0..9 */
    uint8_t span;      /* width in columns (1 or 2) */
    key_action_t act;
    uint32_t code;     /* Unicode codepoint or X11 KeySym */
    const char *label; /* ASCII label if code_label == 0 */
    uint16_t ulabel;   /* Single full-width Unicode glyph label (e.g. Jamo or Arrow) */
} vkey_t;

typedef enum {
    LAYER_EN_LOWER = 0,
    LAYER_EN_UPPER = 1,
    LAYER_KR_BASE  = 2,
    LAYER_KR_SHIFT = 3,
    LAYER_SYM_1    = 4,
    LAYER_SYM_2    = 5,
    LAYER_COUNT    = 6
} layer_id_t;

/* Row 0..2 have 10 keys (span 1 each); Row 3 has 9 keys (Space has span 2) */
#define MAX_KEYS_PER_ROW 10
#define NUM_KEY_ROWS     4

static const vkey_t KEYBOARD_LAYERS[LAYER_COUNT][NUM_KEY_ROWS][MAX_KEYS_PER_ROW] = {
    /* 0: LAYER_EN_LOWER */
    {
        {
            {0,1,ACT_CHAR,'q',"q",0}, {1,1,ACT_CHAR,'w',"w",0}, {2,1,ACT_CHAR,'e',"e",0},
            {3,1,ACT_CHAR,'r',"r",0}, {4,1,ACT_CHAR,'t',"t",0}, {5,1,ACT_CHAR,'y',"y",0},
            {6,1,ACT_CHAR,'u',"u",0}, {7,1,ACT_CHAR,'i',"i",0}, {8,1,ACT_CHAR,'o',"o",0},
            {9,1,ACT_CHAR,'p',"p",0}
        },
        {
            {0,1,ACT_CHAR,'a',"a",0}, {1,1,ACT_CHAR,'s',"s",0}, {2,1,ACT_CHAR,'d',"d",0},
            {3,1,ACT_CHAR,'f',"f",0}, {4,1,ACT_CHAR,'g',"g",0}, {5,1,ACT_CHAR,'h',"h",0},
            {6,1,ACT_CHAR,'j',"j",0}, {7,1,ACT_CHAR,'k',"k",0}, {8,1,ACT_CHAR,'l',"l",0},
            {9,1,ACT_KEYSYM,0xFF08,"BS",0}
        },
        {
            {0,1,ACT_SHIFT,0,"SHF",0}, {1,1,ACT_CHAR,'z',"z",0}, {2,1,ACT_CHAR,'x',"x",0},
            {3,1,ACT_CHAR,'c',"c",0},  {4,1,ACT_CHAR,'v',"v",0}, {5,1,ACT_CHAR,'b',"b",0},
            {6,1,ACT_CHAR,'n',"n",0},  {7,1,ACT_CHAR,'m',"m",0}, {8,1,ACT_CHAR,'.',".",0},
            {9,1,ACT_KEYSYM,0xFF0D,"ENT",0}
        },
        {
            {0,1,ACT_LANG,0,NULL,0xD55C}, {1,1,ACT_SYM,0,"123",0}, {2,1,ACT_CTRL,0,"CTL",0},
            {3,1,ACT_KEYSYM,0xFF1B,"ESC",0}, {4,2,ACT_CHAR,' ',"SPACE",0},
            {6,1,ACT_KEYSYM,0xFF51,NULL,0x2190}, {7,1,ACT_KEYSYM,0xFF54,NULL,0x2193},
            {8,1,ACT_KEYSYM,0xFF52,NULL,0x2191}, {9,1,ACT_KEYSYM,0xFF53,NULL,0x2192},
            {0,0,ACT_NONE,0,NULL,0}
        }
    },
    /* 1: LAYER_EN_UPPER */
    {
        {
            {0,1,ACT_CHAR,'Q',"Q",0}, {1,1,ACT_CHAR,'W',"W",0}, {2,1,ACT_CHAR,'E',"E",0},
            {3,1,ACT_CHAR,'R',"R",0}, {4,1,ACT_CHAR,'T',"T",0}, {5,1,ACT_CHAR,'Y',"Y",0},
            {6,1,ACT_CHAR,'U',"U",0}, {7,1,ACT_CHAR,'I',"I",0}, {8,1,ACT_CHAR,'O',"O",0},
            {9,1,ACT_CHAR,'P',"P",0}
        },
        {
            {0,1,ACT_CHAR,'A',"A",0}, {1,1,ACT_CHAR,'S',"S",0}, {2,1,ACT_CHAR,'D',"D",0},
            {3,1,ACT_CHAR,'F',"F",0}, {4,1,ACT_CHAR,'G',"G",0}, {5,1,ACT_CHAR,'H',"H",0},
            {6,1,ACT_CHAR,'J',"J",0}, {7,1,ACT_CHAR,'K',"K",0}, {8,1,ACT_CHAR,'L',"L",0},
            {9,1,ACT_KEYSYM,0xFF08,"BS",0}
        },
        {
            {0,1,ACT_SHIFT,0,"SHF",0}, {1,1,ACT_CHAR,'Z',"Z",0}, {2,1,ACT_CHAR,'X',"X",0},
            {3,1,ACT_CHAR,'C',"C",0},  {4,1,ACT_CHAR,'V',"V",0}, {5,1,ACT_CHAR,'B',"B",0},
            {6,1,ACT_CHAR,'N',"N",0},  {7,1,ACT_CHAR,'M',"M",0}, {8,1,ACT_CHAR,',',",",0},
            {9,1,ACT_KEYSYM,0xFF0D,"ENT",0}
        },
        {
            {0,1,ACT_LANG,0,NULL,0xD55C}, {1,1,ACT_SYM,0,"123",0}, {2,1,ACT_CTRL,0,"CTL",0},
            {3,1,ACT_KEYSYM,0xFF1B,"ESC",0}, {4,2,ACT_CHAR,' ',"SPACE",0},
            {6,1,ACT_KEYSYM,0xFF51,NULL,0x2190}, {7,1,ACT_KEYSYM,0xFF54,NULL,0x2193},
            {8,1,ACT_KEYSYM,0xFF52,NULL,0x2191}, {9,1,ACT_KEYSYM,0xFF53,NULL,0x2192},
            {0,0,ACT_NONE,0,NULL,0}
        }
    },
    /* 2: LAYER_KR_BASE (2-Bulsik) */
    {
        {
            {0,1,ACT_JAMO,0x3142,NULL,0x3142}, {1,1,ACT_JAMO,0x3148,NULL,0x3148},
            {2,1,ACT_JAMO,0x3137,NULL,0x3137}, {3,1,ACT_JAMO,0x3131,NULL,0x3131},
            {4,1,ACT_JAMO,0x3145,NULL,0x3145}, {5,1,ACT_JAMO,0x315B,NULL,0x315B},
            {6,1,ACT_JAMO,0x3155,NULL,0x3155}, {7,1,ACT_JAMO,0x3151,NULL,0x3151},
            {8,1,ACT_JAMO,0x3150,NULL,0x3150}, {9,1,ACT_JAMO,0x3154,NULL,0x3154}
        },
        {
            {0,1,ACT_JAMO,0x3141,NULL,0x3141}, {1,1,ACT_JAMO,0x3134,NULL,0x3134},
            {2,1,ACT_JAMO,0x3147,NULL,0x3147}, {3,1,ACT_JAMO,0x3139,NULL,0x3139},
            {4,1,ACT_JAMO,0x314E,NULL,0x314E}, {5,1,ACT_JAMO,0x3157,NULL,0x3157},
            {6,1,ACT_JAMO,0x3153,NULL,0x3153}, {7,1,ACT_JAMO,0x314F,NULL,0x314F},
            {8,1,ACT_JAMO,0x3163,NULL,0x3163}, {9,1,ACT_KEYSYM,0xFF08,"BS",0}
        },
        {
            {0,1,ACT_SHIFT,0,"SHF",0},         {1,1,ACT_JAMO,0x314B,NULL,0x314B},
            {2,1,ACT_JAMO,0x314C,NULL,0x314C}, {3,1,ACT_JAMO,0x314A,NULL,0x314A},
            {4,1,ACT_JAMO,0x314D,NULL,0x314D}, {5,1,ACT_JAMO,0x3160,NULL,0x3160},
            {6,1,ACT_JAMO,0x315C,NULL,0x315C}, {7,1,ACT_JAMO,0x3161,NULL,0x3161},
            {8,1,ACT_CHAR,'.',".",0},          {9,1,ACT_KEYSYM,0xFF0D,"ENT",0}
        },
        {
            {0,1,ACT_LANG,0,"EN",0}, {1,1,ACT_SYM,0,"123",0}, {2,1,ACT_CTRL,0,"CTL",0},
            {3,1,ACT_KEYSYM,0xFF1B,"ESC",0}, {4,2,ACT_CHAR,' ',"SPACE",0},
            {6,1,ACT_KEYSYM,0xFF51,NULL,0x2190}, {7,1,ACT_KEYSYM,0xFF54,NULL,0x2193},
            {8,1,ACT_KEYSYM,0xFF52,NULL,0x2191}, {9,1,ACT_KEYSYM,0xFF53,NULL,0x2192},
            {0,0,ACT_NONE,0,NULL,0}
        }
    },
    /* 3: LAYER_KR_SHIFT (Double consonants & ㅒ/ㅖ) */
    {
        {
            {0,1,ACT_JAMO,0x3143,NULL,0x3143}, {1,1,ACT_JAMO,0x3149,NULL,0x3149},
            {2,1,ACT_JAMO,0x3138,NULL,0x3138}, {3,1,ACT_JAMO,0x3132,NULL,0x3132},
            {4,1,ACT_JAMO,0x3146,NULL,0x3146}, {5,1,ACT_JAMO,0x315B,NULL,0x315B},
            {6,1,ACT_JAMO,0x3155,NULL,0x3155}, {7,1,ACT_JAMO,0x3151,NULL,0x3151},
            {8,1,ACT_JAMO,0x3152,NULL,0x3152}, {9,1,ACT_JAMO,0x3156,NULL,0x3156}
        },
        {
            {0,1,ACT_JAMO,0x3141,NULL,0x3141}, {1,1,ACT_JAMO,0x3134,NULL,0x3134},
            {2,1,ACT_JAMO,0x3147,NULL,0x3147}, {3,1,ACT_JAMO,0x3139,NULL,0x3139},
            {4,1,ACT_JAMO,0x314E,NULL,0x314E}, {5,1,ACT_JAMO,0x3157,NULL,0x3157},
            {6,1,ACT_JAMO,0x3153,NULL,0x3153}, {7,1,ACT_JAMO,0x314F,NULL,0x314F},
            {8,1,ACT_JAMO,0x3163,NULL,0x3163}, {9,1,ACT_KEYSYM,0xFF08,"BS",0}
        },
        {
            {0,1,ACT_SHIFT,0,"SHF",0},         {1,1,ACT_JAMO,0x314B,NULL,0x314B},
            {2,1,ACT_JAMO,0x314C,NULL,0x314C}, {3,1,ACT_JAMO,0x314A,NULL,0x314A},
            {4,1,ACT_JAMO,0x314D,NULL,0x314D}, {5,1,ACT_JAMO,0x3160,NULL,0x3160},
            {6,1,ACT_JAMO,0x315C,NULL,0x315C}, {7,1,ACT_JAMO,0x3161,NULL,0x3161},
            {8,1,ACT_CHAR,',',",",0},          {9,1,ACT_KEYSYM,0xFF0D,"ENT",0}
        },
        {
            {0,1,ACT_LANG,0,"EN",0}, {1,1,ACT_SYM,0,"123",0}, {2,1,ACT_CTRL,0,"CTL",0},
            {3,1,ACT_KEYSYM,0xFF1B,"ESC",0}, {4,2,ACT_CHAR,' ',"SPACE",0},
            {6,1,ACT_KEYSYM,0xFF51,NULL,0x2190}, {7,1,ACT_KEYSYM,0xFF54,NULL,0x2193},
            {8,1,ACT_KEYSYM,0xFF52,NULL,0x2191}, {9,1,ACT_KEYSYM,0xFF53,NULL,0x2192},
            {0,0,ACT_NONE,0,NULL,0}
        }
    },
    /* 4: LAYER_SYM_1 (Numbers & Shell operators) */
    {
        {
            {0,1,ACT_CHAR,'1',"1",0}, {1,1,ACT_CHAR,'2',"2",0}, {2,1,ACT_CHAR,'3',"3",0},
            {3,1,ACT_CHAR,'4',"4",0}, {4,1,ACT_CHAR,'5',"5",0}, {5,1,ACT_CHAR,'6',"6",0},
            {6,1,ACT_CHAR,'7',"7",0}, {7,1,ACT_CHAR,'8',"8",0}, {8,1,ACT_CHAR,'9',"9",0},
            {9,1,ACT_CHAR,'0',"0",0}
        },
        {
            {0,1,ACT_CHAR,'-',"-",0}, {1,1,ACT_CHAR,'_',"_",0}, {2,1,ACT_CHAR,'=',"=",0},
            {3,1,ACT_CHAR,'/',"/",0}, {4,1,ACT_CHAR,'\\',"\\",0}, {5,1,ACT_CHAR,'|',"|",0},
            {6,1,ACT_CHAR,'~',"~",0}, {7,1,ACT_CHAR,'\'',"'",0}, {8,1,ACT_CHAR,'"',"\"",0},
            {9,1,ACT_KEYSYM,0xFF08,"BS",0}
        },
        {
            {0,1,ACT_SHIFT,0,"#+=",0}, {1,1,ACT_CHAR,'(',"(",0}, {2,1,ACT_CHAR,')',")",0},
            {3,1,ACT_CHAR,'<',"<",0},  {4,1,ACT_CHAR,'>',">",0}, {5,1,ACT_CHAR,'&',"&",0},
            {6,1,ACT_CHAR,'*',"*",0},  {7,1,ACT_CHAR,';',";",0}, {8,1,ACT_CHAR,':',":",0},
            {9,1,ACT_KEYSYM,0xFF0D,"ENT",0}
        },
        {
            {0,1,ACT_SYM,0,"ABC",0}, {1,1,ACT_KEYSYM,0xFF09,"TAB",0}, {2,1,ACT_CTRL,0,"CTL",0},
            {3,1,ACT_KEYSYM,0xFF1B,"ESC",0}, {4,2,ACT_CHAR,' ',"SPACE",0},
            {6,1,ACT_KEYSYM,0xFF51,NULL,0x2190}, {7,1,ACT_KEYSYM,0xFF54,NULL,0x2193},
            {8,1,ACT_KEYSYM,0xFF52,NULL,0x2191}, {9,1,ACT_KEYSYM,0xFF53,NULL,0x2192},
            {0,0,ACT_NONE,0,NULL,0}
        }
    },
    /* 5: LAYER_SYM_2 (Extended Shell symbols: ! @ # $ % ^ + ` ? , . [ ] { }) */
    {
        {
            {0,1,ACT_CHAR,'!',"!",0}, {1,1,ACT_CHAR,'@',"@",0}, {2,1,ACT_CHAR,'#',"#",0},
            {3,1,ACT_CHAR,'$',"$",0}, {4,1,ACT_CHAR,'%',"%",0}, {5,1,ACT_CHAR,'^',"^",0},
            {6,1,ACT_CHAR,'+',"+",0}, {7,1,ACT_CHAR,'`',"`",0}, {8,1,ACT_CHAR,'?',"?",0},
            {9,1,ACT_CHAR,'.',".",0}
        },
        {
            {0,1,ACT_CHAR,'[',"[",0}, {1,1,ACT_CHAR,']',"]",0}, {2,1,ACT_CHAR,'{',"{",0},
            {3,1,ACT_CHAR,'}',"}",0}, {4,1,ACT_CHAR,',',",",0}, {5,1,ACT_CHAR,'/',"/",0},
            {6,1,ACT_CHAR,'\\',"\\",0}, {7,1,ACT_CHAR,'|',"|",0}, {8,1,ACT_CHAR,'~',"~",0},
            {9,1,ACT_KEYSYM,0xFF08,"BS",0}
        },
        {
            {0,1,ACT_SHIFT,0,"123",0}, {1,1,ACT_CHAR,'-',"-",0}, {2,1,ACT_CHAR,'_',"_",0},
            {3,1,ACT_CHAR,'=',"=",0},  {4,1,ACT_CHAR,'&',"&",0}, {5,1,ACT_CHAR,'*',"*",0},
            {6,1,ACT_CHAR,';',";",0},  {7,1,ACT_CHAR,':',":",0}, {8,1,ACT_CHAR,'"',"\"",0},
            {9,1,ACT_KEYSYM,0xFF0D,"ENT",0}
        },
        {
            {0,1,ACT_SYM,0,"ABC",0}, {1,1,ACT_KEYSYM,0xFF09,"TAB",0}, {2,1,ACT_CTRL,0,"CTL",0},
            {3,1,ACT_KEYSYM,0xFF1B,"ESC",0}, {4,2,ACT_CHAR,' ',"SPACE",0},
            {6,1,ACT_KEYSYM,0xFF51,NULL,0x2190}, {7,1,ACT_KEYSYM,0xFF54,NULL,0x2193},
            {8,1,ACT_KEYSYM,0xFF52,NULL,0x2191}, {9,1,ACT_KEYSYM,0xFF53,NULL,0x2192},
            {0,0,ACT_NONE,0,NULL,0}
        }
    }
};

#endif /* KEYMAP_LAYOUT_H */

