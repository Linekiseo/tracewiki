import { CheckoutController, submitOrder } from "../web/checkout";

test("formats a checkout total", () => {
  expect(submitOrder({ subtotal: 12, region: "US", currency: "USD" })).toContain("$");
});

test("controller delegates to submitOrder", () => {
  const controller = new CheckoutController();
  expect(controller.process({ subtotal: 12, region: "US", currency: "USD" })).toContain("$");
});
