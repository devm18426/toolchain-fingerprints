/* Floats across a call: some targets (PowerPC) only record their float ABI in the
   object's attributes when a floating-point value is passed or returned. */
double scale(double x, float y) { return x * y; }
int main(void) { return (int)scale(1.0, 2.0f) - 2; }
