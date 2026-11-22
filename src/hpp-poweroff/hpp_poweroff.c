/*
 * hpp_poweroff.c — HP Prime G1 (S3C2416) Hardware Power-Off Controller
 *
 * Implements complete hardware shutdown sequence reverse-engineered from
 * PRIME_OS.ROM (0x300056c8, 0x3007ea5c) and BXCBOOT0.BIN (0x1cc):
 *   1. Immediate GPB1 Backlight OFF + LCD FIMD (VIDCON0) stop
 *   2. Safe filesystem sync, swapoff, and read-only rootfs remount (JFFS2 safe)
 *   3. USB Port Charging Mode (disable 5V boost GPF7/GPH13, D+ pull-up GPH14,
 *      suspend USB PHY & disable clocks so external charger IC works cleanly)
 *   4. [On] key (GPG0) release debouncing (100ms continuous LOW)
 *   5. Wake-up configuration on GPG0/EINT8 (Rising Edge), INFORM0..3 = 0 (Cold Boot)
 *   6. S3C2416 Hardware SLEEP Mode (PWRCFG = 0x8101, PWRMODE = 0x2BED) -> CPU OFF
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/mman.h>

#define S3C2416_INTC_PHYS_BASE    0x4A000000U
#define S3C2416_SYSCON_PHYS_BASE  0x4C000000U
#define S3C2416_LCD_PHYS_BASE     0x4C800000U
#define S3C2416_GPIO_PHYS_BASE    0x56000000U

#define REG_PAGE_SIZE             4096U

/* INTC offsets (0x4A000000) */
#define SRCPND_OFF                0x00U
#define INTMSK_OFF                0x08U
#define INTPND_OFF                0x10U
#define INTSUBMSK_OFF             0x1CU
#define SRCPND2_OFF               0x40U
#define INTMSK2_OFF               0x48U
#define INTPND2_OFF               0x50U

/* SYSCON offsets (0x4C000000) */
#define PWRMODE_OFF               0x40U
#define PWRCFG_OFF                0x60U
#define RSTCON_OFF                0x64U
#define WKUPSTAT_OFF              0x6CU
#define INFORM0_OFF               0x70U
#define INFORM1_OFF               0x74U
#define INFORM2_OFF               0x78U
#define INFORM3_OFF               0x7CU
#define PHYPWR_OFF                0x84U
#define UCLKCON_OFF               0x8CU

/* LCD (FIMD) offsets (0x4C800000) */
#define VIDCON0_OFF               0x00U

/* GPIO offsets (0x56000000) */
#define GPBCON_OFF                0x10U
#define GPBDAT_OFF                0x14U
#define GPBUDP_OFF                0x18U
#define GPDCON_OFF                0x30U
#define GPDDAT_OFF                0x34U
#define GPDUDP_OFF                0x38U
#define GPFCON_OFF                0x50U
#define GPFDAT_OFF                0x54U
#define GPGCON_OFF                0x60U
#define GPGDAT_OFF                0x64U
#define GPGUDP_OFF                0x68U
#define GPHCON_OFF                0x70U
#define GPHDAT_OFF                0x74U
#define MISCCR_OFF                0x80U
#define EXTINT0_OFF               0x88U
#define EXTINT1_OFF               0x8CU
#define EINTMASK_OFF              0xA4U
#define EINTPEND_OFF              0xA8U
#define GSTATUS1_OFF              0xB0U

#define REG32(base, off) (*(volatile uint32_t *)((volatile uint8_t *)(base) + (off)))

static void unbind_matrix_keypad(void)
{
    int fd = open("/sys/bus/platform/drivers/matrix-keypad/unbind", O_WRONLY);
    if (fd >= 0) {
        ssize_t ret = write(fd, "matrix-keypad", 13);
        (void)ret;
        close(fd);
    }
}

static void wait_on_key_release(volatile uint8_t *gpio_base)
{
    /* Configure GPD (cols) & GPG (rows, including GPG0=[On]) as Input + Pull-down */
    REG32(gpio_base, GPDCON_OFF) = 0x00000000U;
    REG32(gpio_base, GPDUDP_OFF) = 0x55555555U;
    REG32(gpio_base, GPGCON_OFF) = 0x00000000U;
    REG32(gpio_base, GPGUDP_OFF) = 0x55555555U;

    /* Wait until GPG0 == 0 continuously for 100ms (50 * 2ms), max 5s timeout */
    int low_streak = 0;
    int total_loops = 0;
    while (low_streak < 50 && total_loops < 2500) {
        if ((REG32(gpio_base, GPGDAT_OFF) & (1U << 0)) == 0U) {
            low_streak++;
        } else {
            low_streak = 0;
        }
        usleep(2000);
        total_loops++;
    }
}

static void print_dry_run(volatile uint8_t *syscon_base,
                          volatile uint8_t *intc_base,
                          volatile uint8_t *lcd_base,
                          volatile uint8_t *gpio_base)
{
    uint32_t gstatus1 = REG32(gpio_base, GSTATUS1_OFF);
    uint32_t gpbdat   = REG32(gpio_base, GPBDAT_OFF);
    uint32_t gpfdat   = REG32(gpio_base, GPFDAT_OFF);
    uint32_t gpgdat   = REG32(gpio_base, GPGDAT_OFF);
    uint32_t gphdat   = REG32(gpio_base, GPHDAT_OFF);
    uint32_t misccr   = REG32(gpio_base, MISCCR_OFF);
    uint32_t pwrcfg   = REG32(syscon_base, PWRCFG_OFF);
    uint32_t phypwr   = REG32(syscon_base, PHYPWR_OFF);
    uint32_t uclkcon  = REG32(syscon_base, UCLKCON_OFF);
    uint32_t vidcon0  = REG32(lcd_base, VIDCON0_OFF);
    uint32_t intmsk   = REG32(intc_base, INTMSK_OFF);

    printf("[DRY-RUN] /dev/mem mapped successfully (No poweroff executed)\n");
    printf("  GSTATUS1 (Chip ID) : 0x%08X\n", gstatus1);
    printf("  VIDCON0  (FIMD)    : 0x%08X (ENVID=%u)\n", vidcon0, vidcon0 & 0x3U);
    printf("  GPBDAT   (Backlgt) : 0x%08X (GPB1=%u)\n", gpbdat, (gpbdat >> 1) & 1U);
    printf("  GPFDAT   (OTG/VBUS): 0x%08X (GPF3_CHG=%u, GPF6_ID=%u, GPF7_VBUS1=%u)\n",
           gpfdat, (gpfdat >> 3) & 1U, (gpfdat >> 6) & 1U, (gpfdat >> 7) & 1U);
    printf("  GPGDAT   ([On] key): 0x%08X (GPG0=%u)\n", gpgdat, gpgdat & 1U);
    printf("  GPHDAT   (USB/VBUS): 0x%08X (GPH13_VBUS2=%u, GPH14_DP=%u)\n",
           gphdat, (gphdat >> 13) & 1U, (gphdat >> 14) & 1U);
    printf("  MISCCR             : 0x%08X (SEL_SUSPND=%u)\n", misccr, (misccr >> 12) & 1U);
    printf("  PWRCFG             : 0x%08X (USBPHY=%u)\n", pwrcfg, (pwrcfg >> 4) & 1U);
    printf("  PHYPWR             : 0x%08X\n", phypwr);
    printf("  UCLKCON            : 0x%08X\n", uclkcon);
    printf("  INTMSK             : 0x%08X\n", intmsk);
}

int main(int argc, char *argv[])
{
    bool opt_dry_run = false;
    bool opt_hw_only = false;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--dry-run") || !strcmp(argv[i], "-n")) {
            opt_dry_run = true;
        } else if (!strcmp(argv[i], "--hw-only")) {
            opt_hw_only = true;
        } else if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) {
            printf("Usage: %s [--dry-run|-n] [--hw-only]\n", argv[0]);
            return 0;
        }
    }

    int mem_fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (mem_fd < 0) {
        fprintf(stderr, "hpp-poweroff: failed to open /dev/mem: %s\n", strerror(errno));
        return 1;
    }

    volatile uint8_t *intc_base = (volatile uint8_t *)mmap(
        NULL, REG_PAGE_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, mem_fd, S3C2416_INTC_PHYS_BASE);
    volatile uint8_t *syscon_base = (volatile uint8_t *)mmap(
        NULL, REG_PAGE_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, mem_fd, S3C2416_SYSCON_PHYS_BASE);
    volatile uint8_t *lcd_base = (volatile uint8_t *)mmap(
        NULL, REG_PAGE_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, mem_fd, S3C2416_LCD_PHYS_BASE);
    volatile uint8_t *gpio_base = (volatile uint8_t *)mmap(
        NULL, REG_PAGE_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, mem_fd, S3C2416_GPIO_PHYS_BASE);

    if (intc_base == MAP_FAILED || syscon_base == MAP_FAILED ||
        lcd_base == MAP_FAILED || gpio_base == MAP_FAILED) {
        fprintf(stderr, "hpp-poweroff: mmap hardware registers failed: %s\n", strerror(errno));
        close(mem_fd);
        return 1;
    }

    if (opt_dry_run) {
        print_dry_run(syscon_base, intc_base, lcd_base, gpio_base);
        munmap((void *)intc_base, REG_PAGE_SIZE);
        munmap((void *)syscon_base, REG_PAGE_SIZE);
        munmap((void *)lcd_base, REG_PAGE_SIZE);
        munmap((void *)gpio_base, REG_PAGE_SIZE);
        close(mem_fd);
        return 0;
    }

    /* Prevent concurrent execution if key combo is triggered multiple times */
    int lock_fd = open("/tmp/hpp-poweroff.lock", O_CREAT | O_EXCL | O_RDWR, 0600);
    if (lock_fd < 0 && errno == EEXIST) {
        return 0;
    }

    /* Ignore signals during shutdown so process won't be killed by killall/rcK */
    signal(SIGTERM, SIG_IGN);
    signal(SIGINT, SIG_IGN);
    signal(SIGHUP, SIG_IGN);

    /* Step 1: Immediate visual feedback - turn off GPB1 backlight & stop LCD FIMD */
    REG32(gpio_base, GPBDAT_OFF) &= ~(1U << 1);
    REG32(lcd_base, VIDCON0_OFF) &= ~0x3U;

    /* Step 2: Safe filesystem sync & userspace cleanup */
    if (!opt_hw_only) {
        sync();
        unbind_matrix_keypad();
        int sys_ret = system(
            "killall -q xterm Xorg dropbear dnsmasq udevd syslogd klogd 2>/dev/null; "
            "swapoff -a 2>/dev/null; "
            "sync; "
            "mount -o remount,ro / 2>/dev/null"
        );
        (void)sys_ret;
        sync();
        usleep(100000);
    } else {
        sync();
        unbind_matrix_keypad();
        usleep(50000);
    }

    /* Step 3: LCD FIMD & Backlight final OFF */
    REG32(lcd_base, VIDCON0_OFF) &= ~0x3U;
    REG32(gpio_base, GPBDAT_OFF) = 0x00000060U;
    REG32(gpio_base, GPBCON_OFF) = 0x00141405U;
    REG32(gpio_base, GPBUDP_OFF) = 0x00014010U;

    /* Step 4: USB Port Charging Mode (disable 5V boost, D+ pull-up, suspend PHY) */
    REG32(gpio_base, GPFDAT_OFF) &= ~(1U << 7);
    REG32(gpio_base, GPHDAT_OFF) &= ~((1U << 13) | (1U << 14));
    REG32(syscon_base, PHYPWR_OFF) |= 0x0FU;
    REG32(syscon_base, PWRCFG_OFF) &= ~0x10U;
    REG32(syscon_base, UCLKCON_OFF) = 0x00000000U;
    REG32(gpio_base, MISCCR_OFF) |= (1U << 12);

    /* Step 5: Wait for [On] key (GPG0) release debouncing */
    wait_on_key_release(gpio_base);

    /* Step 6: Mask all interrupts and configure GPG0/EINT8 Rising Edge Wake-up */
    REG32(intc_base, INTMSK_OFF)    = 0xFFFFFFFFU;
    REG32(intc_base, INTSUBMSK_OFF) = 0xFFFFFFFFU;
    REG32(intc_base, INTMSK2_OFF)   = 0xFFFFFFFFU;

    REG32(gpio_base, EXTINT0_OFF)  = 0x466464E4U;
    REG32(gpio_base, EXTINT1_OFF)  = 0x44444444U;
    REG32(gpio_base, GPGCON_OFF)   = 0x00000002U; /* GPG0 = EINT8, rest Input */
    REG32(gpio_base, EINTMASK_OFF) = 0x00FFFEFFU; /* Unmask EINT8 (bit 8) only */
    REG32(gpio_base, EINTPEND_OFF) = 0x00FFFFFFU; /* W1C clear pending */

    REG32(intc_base, SRCPND_OFF)   = 0xFFFFFFFFU;
    REG32(intc_base, INTPND_OFF)   = 0xFFFFFFFFU;
    REG32(intc_base, SRCPND2_OFF)  = 0xFFFFFFFFU;
    REG32(intc_base, INTPND2_OFF)  = 0xFFFFFFFFU;
    REG32(intc_base, INTMSK_OFF)   = 0xFFFFFFDFU; /* Unmask IRQ_EINT8_23 (bit 5) */
    REG32(syscon_base, WKUPSTAT_OFF) = 0xFFFFFFFFU;

    /* Step 7: Boot0 Cold-Boot flags (INFORM2 != 0x55AA) & S3C2416 Hardware SLEEP */
    REG32(syscon_base, INFORM0_OFF) = 0x00000000U;
    REG32(syscon_base, INFORM1_OFF) = 0x00000000U;
    REG32(syscon_base, INFORM2_OFF) = 0x00000000U;
    REG32(syscon_base, INFORM3_OFF) = 0x00000000U;

    REG32(syscon_base, RSTCON_OFF)  = 0x0001FE80U;
    REG32(syscon_base, PWRCFG_OFF)  = 0x00008101U; /* SLEEP | RTC_MASKIRQ | 1 */
    REG32(syscon_base, PWRMODE_OFF) = 0x00002BEDU; /* Go to BED -> CPU OFF */

    while (1) {
        __asm__ volatile("nop");
    }

    return 0;
}

