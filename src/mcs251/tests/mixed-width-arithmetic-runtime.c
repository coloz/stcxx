typedef unsigned char u8;
typedef unsigned long u32;

__sfr __at (0x99) SBUF;

struct node
{
    struct node __xdata *next;
    struct node __xdata *next_free;
};

static struct node __xdata pool[32];
static struct node __xdata * __xdata free_head;

/* These loops deliberately reproduce the register-allocation shape from
   malloc: a 24-bit pointer result reuses the storage of a live 32-bit size.
   With native big-endian spills, result byte zero and size byte one can both
   be the byte at sloc+2.  Writing each result byte immediately used to
   corrupt the source needed by the following add/subtract-with-carry. */
struct node __xdata *
loop_split_plus (u32 n)
{
    struct node __xdata *h;
    struct node __xdata * __xdata *f;

    for (h = free_head, f = &free_head;
         h;
         f = &h->next_free, h = h->next_free)
        {
            u32 blocksize = (u8 __xdata *)h->next - (u8 __xdata *)h;

            if (blocksize >= n)
                {
                    if (blocksize >= n + sizeof (struct node))
                        {
                            struct node __xdata *q =
                              (struct node __xdata *)((u8 __xdata *)h + n);

                            q->next = h->next;
                            q->next_free = h->next_free;
                            *f = q;
                            h->next = q;
                        }
                    else
                        *f = h->next_free;
                    return h;
                }
        }
    return (struct node __xdata *)0;
}

struct node __xdata *
loop_split_plus_reversed (u32 n)
{
    struct node __xdata *h;
    struct node __xdata * __xdata *f;

    for (h = free_head, f = &free_head;
         h;
         f = &h->next_free, h = h->next_free)
        {
            u32 blocksize = (u8 __xdata *)h->next - (u8 __xdata *)h;

            if (blocksize >= n)
                {
                    if (blocksize >= n + sizeof (struct node))
                        {
                            struct node __xdata *q =
                              (struct node __xdata *)(n + (u8 __xdata *)h);

                            q->next = h->next;
                            q->next_free = h->next_free;
                            *f = q;
                            h->next = q;
                        }
                    else
                        *f = h->next_free;
                    return h;
                }
        }
    return (struct node __xdata *)0;
}

struct node __xdata *
loop_split_minus (u32 n)
{
    struct node __xdata *h;
    struct node __xdata * __xdata *f;

    for (h = free_head, f = &free_head;
         h;
         f = &h->next_free, h = h->next_free)
        {
            u32 blocksize = (u8 __xdata *)h->next - (u8 __xdata *)h;

            if (blocksize >= n)
                {
                    if (blocksize >= n + sizeof (struct node))
                        {
                            u8 __xdata *q = (u8 __xdata *)h - n;

                            ((struct node __xdata *)q)->next = h->next;
                            ((struct node __xdata *)q)->next_free =
                              h->next_free;
                            *f = (struct node __xdata *)q;
                            h->next = (struct node __xdata *)q;
                        }
                    else
                        *f = h->next_free;
                    return h;
                }
        }
    return (struct node __xdata *)0;
}

unsigned char
__sdcc_external_startup (void)
{
    return 0;
}

static void
reset_list (struct node __xdata *h, struct node __xdata *end)
{
    h->next = end;
    h->next_free = (struct node __xdata *)0;
    free_head = h;
}

static void
print_result (unsigned char failure)
{
    static const char hex[] = "0123456789abcdef";
    const char *text;

    if (failure)
        {
            SBUF = 'E';
            SBUF = hex[failure >> 4];
            SBUF = hex[failure & 0x0f];
            SBUF = '\n';
        }
    text = failure ? "FAIL\n" : "PASS\n";
    while (*text)
        SBUF = *text++;
}

void
main (void)
{
    struct node __xdata *h;
    struct node __xdata *end;
    struct node __xdata *expected;
    struct node __xdata *result;
    unsigned char failure = 0;
    u32 distance = 3UL * sizeof (struct node);

    h = &pool[4];
    end = &pool[20];
    expected = &pool[7];
    reset_list (h, end);
    result = loop_split_plus (distance);
    if (result != h)
        failure |= 0x01;
    if (free_head != expected || h->next != expected ||
        expected->next != end || expected->next_free != 0)
        failure |= 0x02;

    h = &pool[5];
    end = &pool[21];
    expected = &pool[8];
    reset_list (h, end);
    result = loop_split_plus_reversed (distance);
    if (result != h)
        failure |= 0x04;
    if (free_head != expected || h->next != expected ||
        expected->next != end || expected->next_free != 0)
        failure |= 0x08;

    h = &pool[12];
    end = &pool[24];
    expected = &pool[9];
    reset_list (h, end);
    result = loop_split_minus (distance);
    if (result != h)
        failure |= 0x10;
    if (free_head != expected || h->next != expected ||
        expected->next != end || expected->next_free != 0)
        failure |= 0x20;

    print_result (failure);
    for (;;)
        {
        }
}
