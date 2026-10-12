import { createSorter } from "./sort.js";
let sort;
self.onmessage = ({ data }) => {
  try {
    if (data.type === "model") {
      sort = createSorter(data.model);
      return;
    }
    const order = sort(data.time, data.view, data.near);
    self.postMessage({ type: "sorted", order }, [order.buffer]);
  } catch (error) {
    self.postMessage({ type: "error", message: error.message });
  }
};
