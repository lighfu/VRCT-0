import clsx from "clsx";
import styles from "./StartUpProgressContainer.module.scss";

import { useInitProgress, useResolvedUiTheme } from "@logics_common";
import chat_white_square from "@images/chato_white_square.png";
import vrct_explanation from "@images/vrchat_chatbox_trasnlator_transcription.png";
import vrct_starting_up from "@images/vrct_starting_up.png";
import vrct0_logo_for_dark from "@images/vrct0_logo_stacked_for_dark.png";
import vrct0_logo from "@images/vrct0_logo_stacked.png";

export const StartUpProgressContainer = () => {
    const { currentInitProgress } = useInitProgress();
    const { is_light } = useResolvedUiTheme();

    const progress = currentInitProgress.data;
    return (
        <div className={styles.container}>
            <img src={is_light ? vrct0_logo : vrct0_logo_for_dark} className={styles.logo_img} alt="VRCT-0 logo" />
            <div className={styles.progress_bar_wrapper}>
                {[...Array(4)].map((_, index) => (
                    <div
                        key={index}
                        className={clsx(styles.progress_bar, {
                            [styles.progressed]: index < progress && progress !== 0,
                        })}
                    >
                        {index === 3
                            ?
                            <div className={styles.chato_box}>
                            <img src={chat_white_square} className={styles.chato_img}/>
                            </div>
                            : null
                        }
                    </div>
                ))}
            </div>
            <div className={styles.labels_wrapper}>
                <img src={vrct_starting_up} className={styles.vrct_starting_up_img}/>
                <img src={vrct_explanation} className={styles.vrct_explanation_img}/>
            </div>
        </div>
    );
};
