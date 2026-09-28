import { sortVisible } from "./sort.js?v=5";
let model;
self.onmessage = ({ data }) => {
  try {
    if (data.type === "model") {
      model = data.model;
      return;
    }
    const order = sortVisible(model, data.time, data.view, data.near);
    self.postMessage({ type: "sorted", order }, [order.buffer]);
  } catch (error) {
    self.postMessage({ type: "error", message: error.message });
  }
};
