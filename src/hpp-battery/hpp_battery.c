/*
 * hpp_battery.c — HP Prime G1 (S3C2416) Battery & Power Monitor
 * 
 * Direct hardware register access via /dev/mem to S3C2416 on-chip ADC (0x58000000)
 * and GPIO (0x56000000) based on HP Prime official firmware disassembly analysis.
 *
 * Channel: AIN0 (Channel 0)
 * Multiplier: 0.004556 V/LSB (Hardware voltage divider calibrated)
 * Charging detect: GPF3 (GPFDAT Bit 3)
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>

#define S3C2416_ADC_PHYS_BASE   0x58000000
#define S3C2416_GPIO_PHYS_BASE  0x56000000
#define S3C2416_CLK_PHYS_BASE   0x4C000000
#define MAP_SIZE                4096

/* Clock Registers */
#define PCLKCON_OFF  0x34
#define PCLK_ADC_BIT (1 << 7)

/* ADC Registers */
#define ADCCON_OFF   0x00
#define ADCTSC_OFF   0x04
#define ADCDLY_OFF   0x08
#define ADCDAT0_OFF  0x0C
#define ADCMUX_OFF   0x18

/* GPIO Registers */
#define GPFCON_OFF   0x50
#define GPFDAT_OFF   0x54

/* Battery Conversion Constants (from official PRIME_OS.ROM 0xd60c) */
#define VOLT_MULTIPLIER  0.00455600f
#define V_MIN            3.40f   /* 0% cutoff */
#define V_MAX            4.15f   /* 100% full */

static int read_adc_channel(volatile uint8_t *adc_base, int channel)
{
    volatile uint32_t *adccon  = (volatile uint32_t *)(adc_base + ADCCON_OFF);
    volatile uint32_t *adctsc  = (volatile uint32_t *)(adc_base + ADCTSC_OFF);
    volatile uint32_t *adcdly  = (volatile uint32_t *)(adc_base + ADCDLY_OFF);
    volatile uint32_t *adcdat0 = (volatile uint32_t *)(adc_base + ADCDAT0_OFF);
    volatile uint32_t *adcmux  = (volatile uint32_t *)(adc_base + ADCMUX_OFF);

    /* 1. Setup prescaler (PRSCEN=1, PRSCVL=69 -> 12-bit conversion) */
    *adccon = 0x4580;
    *adcmux  = (uint32_t)channel;
    *adcdly  = 1500;
    *adctsc  = 0xd3;  /* Normal conversion mode */

    /* 2. Start conversion */
    *adccon |= 1;

    /* 3. Wait for start bit clear */
    int timeout = 10000;
    while ((*adccon & 1) && --timeout > 0);

    /* 4. Wait for End of Conversion (ECFLG: Bit 15) */
    timeout = 50000;
    while (!(*adccon & 0x8000) && --timeout > 0);

    if (timeout <= 0)
        return -1;

    /* 5. Read 10/12-bit result (mask lower 12 bits) */
    return (int)(*adcdat0 & 0xFFF);
}

static int sample_adc_trimmed(volatile uint8_t *adc_base, int channel, int samples)
{
    if (samples < 3) samples = 3;
    int sum = 0, min = 99999, max = -1;

    for (int i = 0; i < samples; i++) {
        int v = read_adc_channel(adc_base, channel);
        if (v < 0) return -1;
        sum += v;
        if (v < min) min = v;
        if (v > max) max = v;
        usleep(1000); /* 1ms interval */
    }

    /* Trim min & max */
    sum -= (min + max);
    return sum / (samples - 2);
}

static void print_gauge_bar(int percent, char *bar, int bar_len)
{
    int filled = (percent * bar_len) / 100;
    if (filled > bar_len) filled = bar_len;
    if (filled < 0) filled = 0;

    for (int i = 0; i < bar_len; i++) {
        if (i < filled)
            bar[i] = '=';
        else
            bar[i] = ' ';
    }
    bar[bar_len] = '\0';
}

int main(int argc, char *argv[])
{
    bool opt_percent = false;
    bool opt_voltage = false;
    bool opt_status = false;
    bool opt_json = false;
    bool opt_short = false;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-p") || !strcmp(argv[i], "--percent")) opt_percent = true;
        else if (!strcmp(argv[i], "-v") || !strcmp(argv[i], "--voltage")) opt_voltage = true;
        else if (!strcmp(argv[i], "-s") || !strcmp(argv[i], "--status")) opt_status = true;
        else if (!strcmp(argv[i], "-j") || !strcmp(argv[i], "--json")) opt_json = true;
        else if (!strcmp(argv[i], "-1") || !strcmp(argv[i], "--short")) opt_short = true;
    }

    int fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (fd < 0) {
        perror("Error opening /dev/mem (requires root)");
        return 1;
    }

    volatile uint8_t *clk_base = (volatile uint8_t *)mmap(
        NULL, MAP_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, fd, S3C2416_CLK_PHYS_BASE);
    volatile uint8_t *adc_base = (volatile uint8_t *)mmap(
        NULL, MAP_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, fd, S3C2416_ADC_PHYS_BASE);
    volatile uint8_t *gpio_base = (volatile uint8_t *)mmap(
        NULL, MAP_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, fd, S3C2416_GPIO_PHYS_BASE);

    if (clk_base == MAP_FAILED || adc_base == MAP_FAILED || gpio_base == MAP_FAILED) {
        perror("Error mmap physical registers");
        close(fd);
        return 1;
    }

    /* Ensure S3C2416 ADC Peripheral Clock (PCLKCON Bit 7) is enabled */
    volatile uint32_t *pclkcon = (volatile uint32_t *)(clk_base + PCLKCON_OFF);
    *pclkcon |= PCLK_ADC_BIT;

    /* Read Battery ADC (AIN0) */
    int raw_adc = sample_adc_trimmed(adc_base, 0, 10);
    if (raw_adc < 0) {
        fprintf(stderr, "ADC conversion timeout\n");
        munmap((void *)clk_base, MAP_SIZE);
        munmap((void *)adc_base, MAP_SIZE);
        munmap((void *)gpio_base, MAP_SIZE);
        close(fd);
        return 1;
    }

    /* Read Charging / External VBUS Status (GPF3 / GPFDAT Bit 3) */
    volatile uint32_t *gpfdat = (volatile uint32_t *)(gpio_base + GPFDAT_OFF);
    bool is_external_power = (*gpfdat & (1 << 3)) ? true : false;

    /* Calculate Voltage & Percent */
    float voltage = (float)raw_adc * VOLT_MULTIPLIER;
    int percent = (int)(((voltage - V_MIN) / (V_MAX - V_MIN)) * 100.0f);
    if (percent > 100) percent = 100;
    if (percent < 0) percent = 0;

    const char *status_str = is_external_power ? "Charging (USB)" : "Discharging (Battery)";

    /* Output formats */
    if (opt_percent) {
        printf("%d\n", percent);
    } else if (opt_voltage) {
        printf("%.2f\n", voltage);
    } else if (opt_status) {
        printf("%s\n", is_external_power ? "Charging" : "Discharging");
    } else if (opt_short) {
        printf("[Battery] %.2fV (%d%%) - %s\n", voltage, percent, is_external_power ? "Charging" : "Discharging");
    } else if (opt_json) {
        printf("{\"voltage\": %.2f, \"percent\": %d, \"adc\": %d, \"charging\": %s}\n",
               voltage, percent, raw_adc, is_external_power ? "true" : "false");
    } else {
        char bar[21];
        print_gauge_bar(percent, bar, 20);

        printf("====================================================\n");
        printf("           HP Prime G1 Battery & Power              \n");
        printf("====================================================\n");
        printf("  Battery Volt : %.2f V  (ADC RAW: %d)\n", voltage, raw_adc);
        printf("  Battery Level: %3d%%  [%s]\n", percent, bar);
        printf("  Power Status : %s\n", status_str);
        if (percent <= 15 && !is_external_power) {
            printf("  Warning      : [!] Low Battery! Please connect USB.\n");
        } else {
            printf("  Health       : Good\n");
        }
        printf("====================================================\n");
    }

    munmap((void *)clk_base, MAP_SIZE);
    munmap((void *)adc_base, MAP_SIZE);
    munmap((void *)gpio_base, MAP_SIZE);
    close(fd);
    return 0;
}
