import mongoose from "mongoose";

const connectDB = async (): Promise<void> => {
  try {
    await mongoose.connect(process.env.MONGODB_URI as string);
    console.log(`MongoDB connecté : ${mongoose.connection.host}`);
  } catch (err) {
    console.error("Erreur de connexion MongoDB :", (err as Error).message);
    process.exit(1);
  }
};

export default connectDB;
